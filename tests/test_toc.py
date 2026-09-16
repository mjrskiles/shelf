from pathlib import Path

import pytest

from shelf.cli import main
from shelf.manifest import Document, Manifest, TocEntry
from shelf.toc import build_toc, extract_outline, split_section
from tests.conftest import make_pdf, poppler


def test_split_section() -> None:
    assert split_section("51.4.8 SAI clock generator") == ("51.4.8", "SAI clock generator")
    assert split_section("Section 3.1: Overview") == ("3.1", "Overview")
    assert split_section("A.2 Verified arithmetic") == ("A.2", "Verified arithmetic")
    assert split_section("2 Boot configuration") == ("2", "Boot configuration")
    assert split_section("Table 1. Peripherals versus products") == ("", "Table 1. Peripherals versus products")
    assert split_section("Revision history") == ("", "Revision history")


def test_section_lookup_span_and_enclosing() -> None:
    d = Document(id="d", file="d.pdf", type="reference-manual", pages=30, page_offset=2, toc=[
        TocEntry("1", "Intro", 1, pdf_page=3, level=1),
        TocEntry("1.1", "Scope", 2, pdf_page=4, level=2),
        TocEntry("2", "Registers", 8, pdf_page=10, level=1),
        TocEntry("2.1", "CR1", 9, pdf_page=11, level=2),
        TocEntry("2.2", "CR2", 14, pdf_page=16, level=2),
        TocEntry("3", "Errata", 20, pdf_page=22, level=1),
    ])
    assert d.section("§2.1") is d.toc[3]
    assert d.section("2.1.") is d.toc[3]
    assert d.section("9.9") is None
    assert d.section_span(d.toc[2]) == (10, 21)   # §2 runs until §3
    assert d.section_span(d.toc[3]) == (11, 15)   # §2.1 runs until §2.2
    assert d.section_span(d.toc[5]) == (22, 30)   # last section runs to the end
    assert d.enclosing_section(12) is d.toc[3]
    assert d.enclosing_section(1) is None


@poppler
def test_extract_outline_and_build(tmp_path: Path) -> None:
    pdf = tmp_path / "o.pdf"
    pdf.write_bytes(make_pdf(
        ["cover", "1 Intro", "1.1 Scope", "2 Registers", "Table 1. Regs"],
        outline=[
            ("1 Intro", 2, [("1.1 Scope", 3, [])]),
            ("2 Registers", 4, [("Table 1. Register map", 5, [])]),
        ],
    ))
    items = extract_outline(pdf)
    assert [(i.level, i.title, i.pdf_page) for i in items] == [
        (1, "1 Intro", 2), (2, "1.1 Scope", 3), (1, "2 Registers", 4), (2, "Table 1. Register map", 5),
    ]
    doc = Document(id="o", file="o.pdf", type="datasheet", pages=5, page_offset=1)
    toc = build_toc(doc, items)
    assert [(t.section, t.title, t.page, t.pdf_page, t.level) for t in toc] == [
        ("1", "Intro", 1, 2, 1), ("1.1", "Scope", 2, 3, 2), ("2", "Registers", 3, 4, 1),
    ]
    assert len(build_toc(doc, items, include_tables=True)) == 4
    assert len(build_toc(doc, items, max_level=1)) == 2


@poppler
def test_cli_toc_and_read_by_section(shelf_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(shelf_root)
    pdf = shelf_root / "rm.pdf"
    pdf.write_bytes(make_pdf(
        ["Cover Rev 3", "1 Intro 1/5", "2 FIFO 2/5", "FIFO depth is sixteen 3/5", "3 Errata 4/5"],
        outline=[("1 Intro", 2, []), ("2 FIFO", 3, []), ("3 Errata", 5, [])],
    ))
    assert main(["--root", root, "add", str(pdf), "--id", "rm", "--type", "reference-manual"]) == 0
    assert main(["--root", root, "inspect", "--apply"]) == 0  # offset 1, Rev 3
    assert main(["--root", root, "toc", "rm", "--build"]) == 0
    out = capsys.readouterr().out
    assert "rm: 3 entries from 3 outline items" in out
    assert Manifest.load(shelf_root / "shelf.json").get("rm").toc[1].page == 2  # pdf 3 − offset 1
    assert (shelf_root / "toc" / "rm.json").is_file()
    assert "toc" not in (shelf_root / "shelf.json").read_text()

    assert main(["--root", root, "toc", "rm"]) == 0
    out = capsys.readouterr().out
    assert "§2" in out and "p. 2" in out and "FIFO" in out
    assert main(["--root", root, "toc", "rm", "--grep", "errata"]) == 0
    assert "§3" in capsys.readouterr().out

    # read by section spans until the next entry: §2 = pdf 3..4.
    assert main(["--root", root, "read", "rm", "§2"]) == 0
    out = capsys.readouterr().out
    assert "§2 FIFO" in out and "FIFO depth is sixteen" in out and "Errata" not in out
    assert main(["--root", root, "read", "rm", "9.9"]) == 1  # unknown section → error

    # search hits show their enclosing section.
    assert main(["--root", root, "search", "sixteen"]) == 0
    assert "§2 FIFO" in capsys.readouterr().out

    # a PDF without an outline says so.
    plain = shelf_root / "plain.pdf"
    plain.write_bytes(make_pdf(["no bookmarks here"]))
    assert main(["--root", root, "add", str(plain), "--id", "plain", "--type", "other"]) == 0
    assert main(["--root", root, "toc", "plain", "--build"]) == 1
    assert "no outline" in capsys.readouterr().out

    # verify reports a TOC file nobody owns.
    (shelf_root / "toc" / "ghost.json").write_text('{"id": "ghost", "entries": []}\n')
    assert main(["--root", root, "verify"]) == 1
    assert "ORPHAN   toc/ghost.json" in capsys.readouterr().out
