"""Test fixtures — including a hand-assembled PDF so tests need no PDF library."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal, valid PDF with one line of Helvetica text per page.

    The xref table is computed properly so poppler does not have to
    reconstruct it.
    """
    objects: list[bytes] = []

    def add(body: str) -> int:
        objects.append(body.encode("latin-1"))
        return len(objects)

    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    pages_id = len(objects) + 1 + 2 * len(pages)  # reserved: after all pages + contents
    for text in pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET"
        contents = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        page = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Contents {contents} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>"
        )
        page_ids.append(page)
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    actual_pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    assert actual_pages_id == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


poppler = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler-utils not installed")


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(make_pdf([
        "The USART receive FIFO depth is sixteen entries",
        "VTOR reset value is implementation defined",
        "Errata item 2.2.10 AXI SRAM read corruption",
    ]))
    return path


@pytest.fixture
def shelf_root(tmp_path: Path) -> Path:
    from shelf.manifest import Manifest
    root = tmp_path / "manuals"
    root.mkdir()
    Manifest().save(root / "shelf.json")
    return root
