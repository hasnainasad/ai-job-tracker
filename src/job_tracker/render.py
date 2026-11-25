"""Phase 6: draw a TailoredResume as an A4 PDF in the style of your templates.

Uses ReportLab (the same library your template PDFs were made with) and the Carlito
font (metrically identical to Calibri, free under the SIL Open Font License, bundled in
fonts/). If the resume runs over the page limit, it's re-drawn with slightly smaller
text until it fits, down to a minimum size.
"""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
)

from .models import TailoredResume

NAVY = HexColor("#1F2A44")
BLUE = HexColor("#1F5FBF")
GREY = HexColor("#6B7280")
TEXT = HexColor("#1F2937")

FONT_DIR = Path(__file__).parent / "fonts"
_fonts_registered = False


def _register_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    for name, file in [
        ("Carlito", "Carlito-Regular.ttf"),
        ("Carlito-Bold", "Carlito-Bold.ttf"),
        ("Carlito-Italic", "Carlito-Italic.ttf"),
        ("Carlito-BoldItalic", "Carlito-BoldItalic.ttf"),
    ]:
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
    pdfmetrics.registerFontFamily(
        "Carlito", normal="Carlito", bold="Carlito-Bold", italic="Carlito-Italic", boldItalic="Carlito-BoldItalic"
    )
    _fonts_registered = True


@dataclass
class Contact:
    name: str
    details: list[str]  # e.g. ["London, United Kingdom", "me@example.com", "linkedin.com/in/me"]


def contact_from_master(master_resume: str) -> Contact:
    """Line 1 of the master resume is the name; the first line with an @ holds the contact details."""
    lines = [line.strip() for line in master_resume.splitlines() if line.strip()]
    contact_line = next((line for line in lines[:6] if "@" in line), "")
    return Contact(name=lines[0], details=[p.strip() for p in contact_line.split("|") if p.strip()])


def _styles(size: float) -> dict[str, ParagraphStyle]:
    lead = size * 1.25
    return {
        "name": ParagraphStyle("name", fontName="Carlito-Bold", fontSize=size * 2.4, leading=size * 2.8, textColor=NAVY),
        "headline": ParagraphStyle("headline", fontName="Carlito-Bold", fontSize=size * 1.05, leading=lead, textColor=BLUE),
        "contact": ParagraphStyle("contact", fontName="Carlito", fontSize=size * 0.9, leading=lead, textColor=GREY),
        "section": ParagraphStyle("section", fontName="Carlito-Bold", fontSize=size * 1.05, leading=lead,
                                  textColor=BLUE, spaceBefore=size * 0.9, spaceAfter=1),
        "body": ParagraphStyle("body", fontName="Carlito", fontSize=size, leading=lead, textColor=TEXT),
        "role": ParagraphStyle("role", fontName="Carlito", fontSize=size * 1.05, leading=lead, textColor=TEXT,
                               spaceBefore=size * 0.5, spaceAfter=1),
    }


def _section(title: str, st) -> list:
    return [
        Paragraph(escape(title.upper()), st["section"]),
        HRFlowable(width="100%", thickness=0.6, color=BLUE, spaceBefore=0, spaceAfter=3),
    ]


def _bullets(texts: list[str], st) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(escape(t), st["body"]), leftIndent=12) for t in texts],
        bulletType="bullet", start="•", bulletColor=BLUE, bulletFontName="Carlito",
        bulletFontSize=st["body"].fontSize, leftIndent=12,
    )


def _story(resume: TailoredResume, contact: Contact, size: float) -> list:
    st = _styles(size)
    sep = '  <font color="#9CA3AF">|</font>  '
    story = [
        Paragraph(escape(contact.name), st["name"]),
        Paragraph(sep.join(escape(p.strip()) for p in resume.headline.split("|")), st["headline"]),
        Paragraph(sep.join(escape(d) for d in contact.details), st["contact"]),
    ]
    story += _section("Professional Summary", st) + [Paragraph(escape(resume.summary), st["body"])]
    story += _section("Core Competencies", st) + [
        Paragraph("  •  ".join(escape(c) for c in resume.core_competencies), st["body"])
    ]
    story += _section("Career Highlights", st) + [_bullets([b.text for b in resume.career_highlights], st)]
    story += _section("Professional Experience", st)
    for role in resume.experience:
        story.append(Paragraph(
            f"<b>{escape(role.title)}</b>{sep}<font color='#1F5FBF'><b>{escape(role.company)}</b></font>{sep}"
            f"<i><font color='#6B7280'>{escape(role.dates)}{sep}{escape(role.location)}</font></i>",
            st["role"],
        ))
        story.append(_bullets([b.text for b in role.bullets], st))
    story += _section("Education", st) + [Paragraph(escape(e), st["body"]) for e in resume.education]
    story += _section("Technical Skills", st) + [
        Paragraph(f"<b>{escape(g.category)}:</b> {escape(', '.join(g.items))}", st["body"])
        for g in resume.technical_skills
    ]
    return story


def _build(resume: TailoredResume, contact: Contact, size: float) -> tuple[bytes, int]:
    buf = BytesIO()
    pages = {"n": 0}

    def count(canvas, doc):
        pages["n"] += 1

    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=13 * mm, bottomMargin=13 * mm,
        title=f"{contact.name} - Resume", author=contact.name,
    )
    doc.build(_story(resume, contact, size), onFirstPage=count, onLaterPages=count)
    return buf.getvalue(), pages["n"]


def render_pdf(resume: TailoredResume, contact: Contact, path: str, max_pages: int = 2) -> int:
    """Write the PDF and return its page count (may exceed max_pages if text is too long)."""
    _register_fonts()
    for size in (10.5, 10, 9.5, 9):
        data, n = _build(resume, contact, size)
        if n <= max_pages:
            break
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return n
