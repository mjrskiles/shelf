"""Guess the metadata a human would otherwise type: the printed-page offset
(from running page numbers in headers/footers) and the document revision
(from the cover). Everything guessed is recorded in ``Document.auto`` so
``shelf verify`` keeps asking for confirmation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from shelf.manifest import Document

# "2081/3353", "page 13/73" — numerator is the printed page, denominator the total.
_FRACTION = re.compile(r"(?<![\d.])(\d{1,4})\s*/\s*(\d{1,4})(?![\d.])")
# "page 13", "Page 13", Microchip's "DS22039D-page 13" — without a following
# slash. TI appendices also match ("Addendum-Page 1", "Pack Materials-Page 2");
# those restarting sequences are rejected by the span rule in detect_offset,
# not here, because the hyphen form is legitimate for other vendors.
_PAGE_WORD = re.compile(r"(?<!\w)[Pp]age\s+(\d{1,4})\b(?!\s*/)")

# "(Rev. B)" in a title; "Rev 8", "Revision 2", "Rev. A" in text; "v1.0.5".
_REV_TITLE = re.compile(r"\(\s*(Rev\.?\s*[A-Z0-9]+(?:\.\d+)*)\s*\)", re.I)
_REV_TEXT = re.compile(r"\b(Rev(?:ision)?)\b(\.?)\s*([A-Z]\b|\d+(?:\.\d+)*)", re.I)
_VERSION = re.compile(r"\bv(\d+\.\d+(?:\.\d+)*)\b", re.I)


@dataclass
class Detection:
    value: int | str
    evidence: list[int] = field(default_factory=list)  # pdf pages the guess came from


@dataclass
class Inspection:
    doc_id: str
    offset: Detection | None
    revision: Detection | None


def _edges(text: str, n: int = 4) -> str:
    """Header and footer lines — where page numbers live."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[:3] + lines[-n:])


def detect_offset(pages: list[tuple[int, str]], total: int) -> Detection | None:
    """Solve pdf_page − printed_page from running page numbers.

    Samples the middle of the document (covers and contents pages lie),
    accepts a fraction only if its denominator is the page count, and needs
    agreement across several pages before believing an offset.
    """
    if not pages:
        return None
    if total <= 12:
        sample = pages
    else:
        lo, hi = int(total * 0.2), int(total * 0.9)
        want = 15
        step = max(1, (hi - lo) // want)
        wanted = set(range(lo, hi, step))
        sample = [p for p in pages if p[0] in wanted]

    votes: dict[int, list[int]] = defaultdict(list)
    for pdf_page, text in sample:
        edges = _edges(text)
        printed: list[int] = []
        for m in _FRACTION.finditer(edges):
            num, den = int(m.group(1)), int(m.group(2))
            if den == total and 1 <= num <= total:
                printed.append(num)
        if not printed:
            printed = [int(m.group(1)) for m in _PAGE_WORD.finditer(edges)]
        for pp in printed:
            off = pdf_page - pp
            if 0 <= off <= 60:
                votes[off].append(pdf_page)

    if not votes:
        return None
    best = max(votes.items(), key=lambda kv: len(kv[1]))
    n_votes = sum(len(v) for v in votes.values())
    need = 2 if total <= 20 else 3
    if len(best[1]) < need or len(best[1]) / n_votes < 0.6:
        return None
    evidence = sorted(set(best[1]))
    # A few consecutive pages agreeing is what an appendix with its own
    # numbering looks like; real page numbers agree across the document.
    if total > 6 and (evidence[-1] - evidence[0]) < total * 0.25:
        return None
    return Detection(best[0], evidence)


def _normalise_rev(word: str, dot: str, token: str) -> str:
    return f"Rev{dot} {token}"


def detect_revision(title: str, cover_pages: list[tuple[int, str]]) -> Detection | None:
    """Revision from the PDF title or the first pages of text."""
    m = _REV_TITLE.search(title)
    if m:
        inner = m.group(1)
        mm = _REV_TEXT.search(inner)
        if mm:
            return Detection(_normalise_rev(*mm.groups()), [])
    for pdf_page, text in cover_pages:
        for mm in _REV_TEXT.finditer(text):
            token = mm.group(3)
            if re.fullmatch(r"(19|20)\d{2}", token):  # "Rev 2025" is a date, not a revision
                continue
            return Detection(_normalise_rev(*mm.groups()), [pdf_page])
        mv = _VERSION.search(text)
        if mv:
            return Detection(f"v{mv.group(1)}", [pdf_page])
    return None


def inspect(doc: Document, pages: list[tuple[int, str]]) -> Inspection:
    return Inspection(
        doc_id=doc.id,
        offset=detect_offset(pages, doc.pages),
        revision=detect_revision(doc.title, pages[:2]),
    )


def apply(doc: Document, result: Inspection, force: bool = False) -> list[str]:
    """Write guesses into ``doc`` for fields that are unknown (or all, with
    ``force``). Returns the field names changed; marks them in ``doc.auto``."""
    changed: list[str] = []
    if result.offset is not None and (force or doc.page_offset is None):
        doc.page_offset = int(result.offset.value)
        changed.append("page_offset")
    if result.revision is not None and (force or doc.revision == "unknown"):
        doc.revision = str(result.revision.value)
        changed.append("revision")
    for name in changed:
        if name not in doc.auto:
            doc.auto.append(name)
    return changed
