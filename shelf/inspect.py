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


# The JAES running footer, which OCR renders with the spaces eaten:
# "J. AudioEng.Soc.,Vol.35,No.12,1987December". Every token is separated by
# \s* rather than \s+ for exactly that reason. This is the publication of
# record and outranks a convention line when both appear.
_JAES = re.compile(
    r"J\.\s*Audio\s*Eng\.\s*Soc\.\s*,?\s*Vol\.\s*(\d+)\s*,?\s*No\.\s*(\d+)\s*,?\s*((?:19|20)\d{2})",
    re.I,
)
# "Presented at the 76th Convention of the Audio Engineering Society, New
# York, 1984 October 8-11" and the all-caps preprint cover variant.
_CONVENTION = re.compile(r"Presented\s+at\s+the\s+(\d+\s*(?:st|nd|rd|th))\s+Convention", re.I)
_CONV_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

# An author line is all-caps words and nothing else: no digits, no lowercase.
_CAPS_LINE = re.compile(r"^[A-Z][A-Z.\-']*(?:\s+[A-Z][A-Z.\-']*)*$")
# A personal name in these bylines carries an initial — "RICHARD C. HEYSER",
# "R. A. GREINER". Requiring one somewhere in the line is what separates a
# byline from an all-caps *title*, which is otherwise the same shape:
# "SPECIFIC ACOUSTIC WAVE ADMITTANCE" parses as three perfectly good names.
# It costs recall on plain two-token bylines ("KARLHEINZ BRANDENBURG"), which
# is the correct trade: a missing byline is a prompt, a wrong one is a lie.
_INITIAL = re.compile(r"\b[A-Z]\.")
# Function words never appear in a byline but are everywhere in a title.
_TITLE_WORDS = {
    "OF", "THE", "IN", "FOR", "WITH", "AS", "ON", "TO", "A", "AN", "BY",
    "FROM", "AT", "INTO", "USING", "VIA", "AND", "OR", "NEW",
}
# All-caps furniture that is not a byline.
_NOT_AUTHORS = (
    "PAPER", "PREPRINT", "CONVENTION", "SOCIETY", "ENGINEERING", "ABSTRACT",
    "INTRODUCTION", "PRESENTED", "REPRINT", "JOURNAL", "AUDIO", "VOL", "NO.",
    "CONTENTS", "REFERENCES", "APPENDIX", "SUMMARY", "ENGINEER",
)


@dataclass
class Biblio:
    authors: list[str] = field(default_factory=list)
    year: int = 0
    venue: str = ""
    evidence: list[int] = field(default_factory=list)


def detect_biblio(cover_pages: list[tuple[int, str]]) -> Biblio | None:
    """Authors, year, and venue from a paper's cover page.

    Conservative on purpose: it returns only what it can see, leaves the rest
    at its zero value, and returns None when it sees nothing at all. A paper
    whose cover does not follow one of these two AES layouts is left for a
    human — a wrong byline is worse than a missing one.
    """
    out = Biblio()
    for pdf_page, text in cover_pages:
        if not out.year:
            m = _JAES.search(text)
            if m:
                out.venue = out.venue or "JAES"
                out.year = int(m.group(3))
                out.evidence.append(pdf_page)
            else:
                mc = _CONVENTION.search(text)
                if mc:
                    ordinal = "".join(mc.group(1).split()).lower()
                    out.venue = out.venue or f"AES {ordinal} Convention"
                    near = text[mc.end() : mc.end() + 120]
                    my = _CONV_YEAR.search(near)
                    if my:
                        out.year = int(my.group(1))
                    out.evidence.append(pdf_page)
        if not out.authors:
            out.authors = _detect_authors(text)
            if out.authors and pdf_page not in out.evidence:
                out.evidence.append(pdf_page)
    if not (out.authors or out.year or out.venue):
        return None
    return out


def _detect_authors(text: str) -> list[str]:
    lines = [" ".join(ln.split()) for ln in text.splitlines()[:40]]
    for ln in lines:
        if not (2 <= len(ln) <= 90) or not _CAPS_LINE.match(ln):
            continue
        if any(bad in ln for bad in _NOT_AUTHORS):
            continue
        if not _INITIAL.search(ln):
            continue
        names = [n.strip() for n in re.split(r"\s+AND\s+|,(?!\s*[A-Z]\.)", ln) if n.strip()]
        # A byline is people: each is at least a given name and a surname, and
        # none of them is built out of the function words a title runs on.
        if not names or not all(len(n.split()) >= 2 for n in names):
            continue
        if any(tok in _TITLE_WORDS for n in names for tok in n.split()):
            continue
        # "U. S. A." on an address line is initials all the way down; a person
        # has a surname somewhere.
        if not all(any(len(tok.rstrip(".")) > 1 for tok in n.split()) for n in names):
            continue
        return names
    return []


@dataclass
class Detection:
    value: int | str
    evidence: list[int] = field(default_factory=list)  # pdf pages the guess came from


@dataclass
class Inspection:
    doc_id: str
    offset: Detection | None
    revision: Detection | None
    biblio: Biblio | None = None


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
    """A paper is dated and attributed, not revised; anything else is revised."""
    return Inspection(
        doc_id=doc.id,
        offset=detect_offset(pages, doc.pages),
        revision=None if doc.is_paper else detect_revision(doc.title, pages[:2]),
        biblio=detect_biblio(pages[:2]) if doc.is_paper else None,
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
    if result.biblio is not None:
        b = result.biblio
        if b.authors and (force or not doc.authors):
            doc.authors = list(b.authors)
            changed.append("authors")
        if b.year and (force or not doc.year):
            doc.year = b.year
            changed.append("year")
        if b.venue and (force or not doc.venue):
            doc.venue = b.venue
            changed.append("venue")
    for name in changed:
        if name not in doc.auto:
            doc.auto.append(name)
    return changed
