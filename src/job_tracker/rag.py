"""Phase 7e: RAG (retrieval-augmented generation) = search by meaning, then give the LLM
only the few pieces that matter.

Novice view
-----------
1. Chunking: long texts are cut into pieces (a job's headline facts, then its posting in
   ~1,200-character slices; your master resume bullet by bullet).
2. Embedding: each piece becomes a vector (embeddings.py).
3. Indexing: the vectors go into Chroma, a small local vector database (folder chroma/,
   git-ignored, like tracker.db).
4. Retrieval: a question is embedded the same way, and Chroma returns the nearest pieces.

What uses it:
  find_jobs("fintech roles with design-system work")   your saved jobs, by meaning
  similar_jobs(job_id)                                  "more like this one"
  requirement_evidence(job)                             per requirement, your best resume bullets
                                                        (the CV reviewer gets these)

The index keeps itself up to date: before every search, jobs that are new or changed
since they were indexed are embedded (a content hash tells which), so there is no
separate step after adding jobs. --reindex rebuilds everything.

Expert notes: one collection per embedding model; cosine distance; a job's score is its
best-matching chunk (max-pooling), so a single relevant paragraph in a long posting counts.
"""
import hashlib
import os
import re
from dataclasses import dataclass

from sqlmodel import Session

from . import db
from .embeddings import collection_name, make_embedder
from .models import Job

CHUNK_CHARS = 1200
OVERLAP = 200


@dataclass
class Chunk:
    id: str
    text: str
    meta: dict


@dataclass
class Hit:
    job_id: str
    score: float      # 0-1, higher = closer in meaning
    text: str         # the best-matching piece
    title: str = ""
    company: str = ""


@dataclass
class Evidence:
    text: str
    role: str
    score: float


# ---------------------------------------------------------------- chunking

def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Slices of about `size` characters, cut at paragraph or sentence ends where possible."""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    pieces, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in pieces if p]


def job_header(job: Job) -> str:
    """The job's key facts in one short text: what a searcher usually means."""
    lines = [f"{job.title} at {job.company}", job.location]
    if job.insights:
        i = job.insights
        lines += [i.summary, f"Work mode: {i.remote_policy}",
                  "Must have: " + ", ".join(i.must_have_skills),
                  "Nice to have: " + ", ".join(i.nice_to_have_skills)]
    if job.salary:
        lines.append(f"Salary: {job.salary}")
    return "\n".join(line for line in lines if line and not line.endswith(": "))


def job_chunks(job: Job) -> list[Chunk]:
    meta = {"kind": "job", "job_id": job.job_id, "title": job.title, "company": job.company}
    texts = [job_header(job)] + split_text(job.description)
    return [Chunk(f"job:{job.job_id}:{n}", t, {**meta, "part": n}) for n, t in enumerate(texts)]


_ROLE = re.compile(r"^[^•\-*|][^|]{2,}\|[^|]+\|")       # "Technical Lead | HSBC | dates | place"
_BULLET = re.compile(r"^\s*[•\-*]\s+")
_LABEL = re.compile(r"^[A-Z][^:•|]{2,40}:\s")             # "Frontend & Architecture: React, ..."


def resume_chunks(master: str) -> list[Chunk]:
    """One chunk per bullet (continuation lines joined), plus the summary and competencies.
    Each remembers the role or section it came from."""
    chunks: list[Chunk] = []
    section = role = ""
    current: list[str] = []

    def flush():
        if current:
            text = " ".join(" ".join(current).split())
            if len(text.split()) >= 5:
                for piece in split_text(text, size=600, overlap=0):  # a very long paragraph -> pieces
                    chunks.append(Chunk(f"resume:{_hash(piece)}", piece, {"kind": "resume", "role": role or section}))
            current.clear()

    for raw in master.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.isupper() and len(line) < 60:           # SECTION HEADING
            flush()
            section, role = line.title(), ""
            continue
        if _ROLE.match(line):
            flush()
            role = line.split("|")[0].strip() + " @ " + line.split("|")[1].strip()
            continue
        if _BULLET.match(line) or _LABEL.match(line):     # a bullet, or a "Label: items" line
            flush()
            current.append(_BULLET.sub("", line))
        elif current or section:                          # continuation, or summary text
            current.append(line)
    flush()
    return chunks


# ---------------------------------------------------------------- the index

class Index:
    """Chroma collections for jobs and resume bullets, for one embedding model."""

    def __init__(self, path: str | None = None, embedder=None):
        import chromadb

        self.embedder = embedder or make_embedder()
        self.client = chromadb.PersistentClient(path=path or os.getenv("CHROMA_PATH", "chroma"),
                                                settings=chromadb.Settings(anonymized_telemetry=False))
        self.jobs = self._collection("jobs")
        self.resume = self._collection("resume")

    def _collection(self, prefix: str):
        return self.client.get_or_create_collection(collection_name(prefix, self.embedder),
                                                    metadata={"hnsw:space": "cosine"}, embedding_function=None)

    def _add(self, col, chunks: list[Chunk]) -> None:
        if chunks:
            vectors = self.embedder.embed([c.text for c in chunks])
            col.upsert(ids=[c.id for c in chunks], embeddings=vectors,
                       documents=[c.text for c in chunks], metadatas=[c.meta for c in chunks])

    # -- keeping it up to date
    def index_jobs(self, jobs: list[Job], force: bool = False) -> int:
        """Embed jobs that are new or changed since last time. Returns how many were (re)indexed."""
        indexed = {}
        if not force and jobs:
            got = self.jobs.get(where={"part": 0}, include=["metadatas"])
            indexed = {m["job_id"]: m.get("hash") for m in got["metadatas"]}
        todo = []
        for job in jobs:
            chunks = job_chunks(job)
            digest = _hash("\n".join(c.text for c in chunks))
            if indexed.get(job.job_id) == digest:
                continue
            chunks[0].meta["hash"] = digest
            todo.append((job.job_id, chunks))
        for job_id, _ in todo:
            self.jobs.delete(where={"job_id": job_id})  # a shorter posting leaves no stale slices
        self._add(self.jobs, [c for _, chunks in todo for c in chunks])
        return len(todo)

    def index_resume(self, master: str) -> int:
        chunks = resume_chunks(master)
        have = set(self.resume.get(include=[])["ids"])
        if have != {c.id for c in chunks}:
            if have:
                self.resume.delete(ids=list(have))
            self._add(self.resume, chunks)
        return len(chunks)

    def refresh(self, session: Session, profile_dir: str = "profile") -> int:
        """Bring the index up to date with the database (and the master resume, if present)."""
        n = self.index_jobs(db.load_all(session))
        master = os.path.join(profile_dir, "master_resume.md")
        if os.path.exists(master):
            with open(master, encoding="utf-8") as f:
                self.index_resume(f.read())
        return n

    def rebuild(self, session: Session, profile_dir: str = "profile") -> int:
        for name in (self.jobs.name, self.resume.name):
            self.client.delete_collection(name)
        self.jobs, self.resume = self._collection("jobs"), self._collection("resume")
        return self.refresh(session, profile_dir)

    # -- retrieval
    def _best_per_job(self, result, exclude: str | None, limit: int) -> list[Hit]:
        best: dict[str, Hit] = {}
        for text, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0],
                                    strict=True):
            jid = meta["job_id"]
            if jid == exclude:
                continue
            score = round(1 - dist, 3)
            if jid not in best or score > best[jid].score:
                best[jid] = Hit(jid, score, text, meta.get("title", ""), meta.get("company", ""))
        return sorted(best.values(), key=lambda h: h.score, reverse=True)[:limit]

    def find_jobs(self, query: str, limit: int = 10) -> list[Hit]:
        n = self.jobs.count()
        if not n:
            return []
        result = self.jobs.query(query_embeddings=self.embedder.embed([query]), n_results=min(n, limit * 5),
                                 include=["documents", "metadatas", "distances"])
        return self._best_per_job(result, None, limit)

    def similar_jobs(self, job_id: str, limit: int = 5) -> list[Hit]:
        got = self.jobs.get(ids=[f"job:{job_id}:0"], include=["embeddings"])
        if not len(got["ids"]):
            return []
        n = self.jobs.count()
        result = self.jobs.query(query_embeddings=[list(got["embeddings"][0])], where={"part": 0},
                                 n_results=min(n, limit + 1), include=["documents", "metadatas", "distances"])
        return self._best_per_job(result, job_id, limit)

    def evidence(self, texts: list[str], per_item: int = 3) -> list[list[Evidence]]:
        """For each text (e.g. a job requirement), the closest bullets of your master resume."""
        n = self.resume.count()
        if not n or not texts:
            return [[] for _ in texts]
        result = self.resume.query(query_embeddings=self.embedder.embed(texts), n_results=min(n, per_item),
                                   include=["documents", "metadatas", "distances"])
        return [[Evidence(d, m.get("role", ""), round(1 - x, 3)) for d, m, x in zip(docs, metas, dists, strict=True)]
                for docs, metas, dists in zip(result["documents"], result["metadatas"], result["distances"],
                                              strict=True)]


def requirements(job: Job, limit: int = 8) -> list[str]:
    """What the job asks for: the AI-extracted must-haves, else the fit gaps, else its headline."""
    if job.insights and job.insights.must_have_skills:
        return job.insights.must_have_skills[:limit]
    if job.fit and job.fit.key_gaps:
        return job.fit.key_gaps[:limit]
    return [job_header(job)]


def requirement_evidence(index: Index, job: Job, per_item: int = 3) -> list[tuple[str, list[Evidence]]]:
    reqs = requirements(job)
    return list(zip(reqs, index.evidence(reqs, per_item), strict=True))


def evidence_text(pairs: list[tuple[str, list[Evidence]]]) -> str:
    lines = []
    for req, found in pairs:
        lines.append(f"- {req}")
        lines += [f"    [{e.score:.2f}] ({e.role}) {e.text}" for e in found]
    return "\n".join(lines)


def open_index(path: str | None = None, embedder=None) -> Index:
    return Index(path, embedder)
