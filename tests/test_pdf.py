from pathlib import Path

from shelf.pdf import extract_pages, pdf_info, sha256_file, text_layer_quality
from tests.conftest import poppler


@poppler
def test_pdf_info_counts_pages(sample_pdf: Path) -> None:
    assert pdf_info(sample_pdf).pages == 3


@poppler
def test_extract_pages_one_entry_per_page(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    assert len(pages) == 3
    assert "FIFO" in pages[0]
    assert "VTOR" in pages[1]
    assert "2.2.10" in pages[2]


def test_sha256_is_stable(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert sha256_file(f) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_text_layer_quality_buckets() -> None:
    good = ["word " * 40] * 10
    scanned = [""] * 10
    mixed = ["word " * 40] * 5 + [""] * 5
    assert text_layer_quality(good) == "good"
    assert text_layer_quality(scanned) == "poor"
    assert text_layer_quality(mixed) == "partial"
    assert text_layer_quality([]) == "poor"
