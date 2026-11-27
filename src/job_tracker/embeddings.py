"""Phase 7e: embeddings = text -> a list of numbers that captures its meaning.

Texts that mean similar things get similar numbers, even with no words in common
("design systems lead" ~ "owns the component library"). Comparing two embeddings
(cosine similarity) says how close their meanings are: 1.0 = same, ~0 = unrelated.

Two providers behind one method, .embed(texts) -> vectors (EMBED_PROVIDER in .env):
  openai   text-embedding-3-small (default). Uses OPENAI_API_KEY. ~$0.02 per million
           tokens: your whole tracker costs a fraction of a cent.
  ollama   runs on your Mac, free, nothing leaves it. Install Ollama, then
           `ollama pull nomic-embed-text`.
Vectors from different models can't be compared, so each model gets its own collection
in the index; switching provider just means one --reindex.
"""
import os
import re


class OpenAIEmbedder:
    BATCH = 100

    def __init__(self, model: str | None = None):
        from openai import OpenAI

        self.model = model or os.getenv("EMBED_MODEL") or "text-embedding-3-small"
        self.client = OpenAI()
        self.name = f"openai-{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            r = self.client.embeddings.create(model=self.model, input=texts[i:i + self.BATCH])
            out += [d.embedding for d in r.data]
        return out


class OllamaEmbedder:
    def __init__(self, model: str | None = None, url: str | None = None):
        self.model = model or os.getenv("EMBED_MODEL") or "nomic-embed-text"
        self.url = (url or os.getenv("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
        self.name = f"ollama-{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        import requests

        try:
            r = requests.post(f"{self.url}/api/embed", json={"model": self.model, "input": texts}, timeout=120)
        except requests.ConnectionError as e:
            raise RuntimeError(f"Ollama isn't running at {self.url} (start the Ollama app)") from e
        if r.status_code != 200:
            raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:200]} (did you `ollama pull {self.model}`?)")
        return r.json()["embeddings"]


def make_embedder(provider: str | None = None):
    provider = (provider or os.getenv("EMBED_PROVIDER", "openai")).lower()
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("search by meaning needs OPENAI_API_KEY in .env (or EMBED_PROVIDER=ollama)")
        return OpenAIEmbedder()
    if provider == "ollama":
        return OllamaEmbedder()
    raise ValueError(f"Unknown EMBED_PROVIDER '{provider}'. Use 'openai' or 'ollama'.")


def collection_name(prefix: str, embedder) -> str:
    """Chroma collection per model: e.g. jobs-openai-text-embedding-3-small."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", f"{prefix}-{embedder.name}")[:200].strip("-._")
