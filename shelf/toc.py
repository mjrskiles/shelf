"""Tables of contents from PDF outlines (bookmarks).

``pdftohtml -xml`` emits the outline as nested ``<outline><item page="N">``
elements without rendering pages, so this is cheap even for a 3000-page
reference manual. Documents without bookmarks get nothing — an honest
"no outline" rather than a guessed structure.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from shelf import ShelfError
from shelf.manifest import Document, TocEntry


@dataclass
class OutlineItem:
    level: int
    title: str
    pdf_page: int


def require_pdftohtml() -> None:
    if shutil.which("pdftohtml") is None:
        raise ShelfError("pdftohtml not found (part of poppler-utils)")


def extract_outline(path: Path) -> list[OutlineItem]:
    """Read the PDF's bookmark outline in document order, with nesting depth."""
    require_pdftohtml()
    try:
        out = subprocess.run(
            ["pdftohtml", "-xml", "-i", "-f", "1", "-l", "1", "-stdout", str(path)],
            capture_output=True, text=True, check=True, errors="replace",
        ).stdout
    except subprocess.CalledProcessError as e:
        raise ShelfError(f"pdftohtml failed on {path}: {e.stderr.strip()}") from None
    try:
        root = ET.fromstring(out)
    except ET.ParseError as e:
        raise ShelfError(f"could not parse pdftohtml output for {path}: {e}") from None

    items: list[OutlineItem] = []

    def walk(node: ET.Element, level: int) -> None:
        for child in node:
            if child.tag == "item":
                page = int(child.get("page", "0") or 0)
                title = " ".join((child.text or "").split())
                if title:
                    items.append(OutlineItem(level, title, page))
            elif child.tag == "outline":
                walk(child, level + 1)

    # Walk only top-level <outline> elements; nested ones are reached by walk().
    parent_of = {child: parent for parent in root.iter() for child in parent}
    for top in root.iter("outline"):
        if parent_of.get(top) is not None and parent_of[top].tag == "outline":
            continue
        walk(top, 1)
    return items


# "51.4.8 SAI clock generator", "A.2 Verified arithmetic", "Section 3.1 Foo",
# "Chapter 2 — Bar", "3 Overview". Not "Table 1. Foo" or "Figure 2. Bar".
_SECTION = re.compile(
    r"^(?:(?:Section|Chapter|Appendix)\s+)?([A-Z]?\d+(?:\.\d+)*[a-z]?|[A-Z](?:\.\d+)+)"
    r"[\s:.\-–—]+(.+)$"
)
_FIGURE_TABLE = re.compile(r"^(Table|Figure|Fig\.|Listing|Equation)\s+[A-Z]?\d", re.I)


def split_section(title: str) -> tuple[str, str]:
    """Split a heading into (section number, title). Unnumbered → ("", title)."""
    if _FIGURE_TABLE.match(title):
        return "", title
    m = _SECTION.match(title)
    if not m:
        return "", title
    return m.group(1), m.group(2).strip()


def is_figure_or_table(title: str) -> bool:
    return bool(_FIGURE_TABLE.match(title))


def build_toc(doc: Document, items: list[OutlineItem], include_tables: bool = False,
              max_level: int | None = None) -> list[TocEntry]:
    offset = doc.page_offset or 0
    toc: list[TocEntry] = []
    for it in items:
        if not include_tables and is_figure_or_table(it.title):
            continue
        if max_level is not None and it.level > max_level:
            continue
        section, name = split_section(it.title)
        toc.append(TocEntry(section=section, title=name, page=it.pdf_page - offset,
                            pdf_page=it.pdf_page, level=it.level))
    return toc
