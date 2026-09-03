"""PDF inspection and text extraction via poppler's command-line tools."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shelf import ShelfError

_TOOLS = ("pdfinfo", "pdftotext")


def require_poppler() -> None:
    missing = [t for t in _TOOLS if shutil.which(t) is None]
    if missing:
        raise ShelfError(
            f"poppler tools not found: {', '.join(missing)} "
            f"(Debian/Ubuntu: apt install poppler-utils; macOS: brew install poppler)"
        )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class PdfInfo:
    title: str
    pages: int


def pdf_info(path: Path) -> PdfInfo:
    require_poppler()
    try:
        out = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, text=True, check=True
        ).stdout
    except subprocess.CalledProcessError as e:
        raise ShelfError(f"pdfinfo failed on {path}: {e.stderr.strip()}") from None
    title = ""
    pages = 0
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if key == "Title":
            title = value.strip()
        elif key == "Pages":
            pages = int(value.strip() or 0)
    if pages == 0:
        raise ShelfError(f"{path}: pdfinfo reported no pages — not a PDF?")
    return PdfInfo(title=title, pages=pages)


def extract_pages(path: Path) -> list[str]:
    """Return the text layer of every page, in order (index 0 = PDF page 1).

    pdftotext separates pages with form feeds; a trailing form feed follows
    the last page.
    """
    require_poppler()
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, check=True, errors="replace",
        ).stdout
    except subprocess.CalledProcessError as e:
        raise ShelfError(f"pdftotext failed on {path}: {e.stderr.strip()}") from None
    pages = out.split("\f")
    if pages and pages[-1].strip() == "":
        pages.pop()
    return pages


def extract_page_range(path: Path, first: int, last: int) -> str:
    require_poppler()
    try:
        return subprocess.run(
            ["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(path), "-"],
            capture_output=True, text=True, check=True, errors="replace",
        ).stdout
    except subprocess.CalledProcessError as e:
        raise ShelfError(f"pdftotext failed on {path}: {e.stderr.strip()}") from None


_WORD = re.compile(r"[A-Za-z]{3,}")


def text_layer_quality(pages: list[str]) -> str:
    """Rough classification of how usable the text layer is.

    A page counts as readable if it has at least twenty three-letter-or-longer
    words. Scanned or vector-only pages have none.
    """
    if not pages:
        return "poor"
    readable = sum(1 for p in pages if len(_WORD.findall(p)) >= 20)
    ratio = readable / len(pages)
    if ratio >= 0.8:
        return "good"
    if ratio >= 0.3:
        return "partial"
    return "poor"
