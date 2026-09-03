import shutil
from pathlib import Path

import pytest

from shelf import ShelfError
from shelf.cli import main
from shelf.index import Index, _phrase_query
from shelf.manifest import Document, Manifest
from tests.conftest import poppler


def test_phrase_query_quotes_tokens() -> None:
    assert _phrase_query('FIFO depth') == '"FIFO" "depth"'
    assert _phrase_query('say "hi"') == '"say" """hi"""'
    with pytest.raises(ShelfError):
        _phrase_query("   ")


def test_index_search_applies_page_offset(tmp_path: Path) -> None:
    m = Manifest()
    doc = Document(id="d", file="d.pdf", type="datasheet", parts=["ABC123"], sha256="s", pages=3, page_offset=2)
    m.add(doc)
    idx = Index(tmp_path / "catalog.db")
    idx.index_document(doc, ["front matter", "more front matter", "the FIFO depth is sixteen"])
    hits = idx.search("fifo depth", m)
    assert len(hits) == 1
    assert hits[0].pdf_page == 3
    assert hits[0].printed_page == 1
    assert "[FIFO]" in hits[0].snippet
    assert idx.search("fifo depth", m, part="abc") == hits
    assert idx.search("fifo depth", m, part="xyz") == []
    assert idx.search("fifo depth", m, doc_id="other") == []
    idx.close()


def test_index_build_skips_unchanged_and_drops_orphans(tmp_path: Path) -> None:
    m = Manifest()
    doc = Document(id="d", file="d.pdf", type="datasheet", sha256="s", pages=1)
    m.add(doc)
    idx = Index(tmp_path / "catalog.db")
    idx.index_document(doc, ["text"])
    # Missing file: build should report it, not crash, and keep the stale entry out.
    stats = idx.build(m, tmp_path)
    assert stats.missing == ["d"]
    # A document removed from the manifest disappears from the index.
    m.remove("d")
    idx.build(m, tmp_path)
    assert idx.indexed_ids() == []
    idx.close()


@poppler
def test_cli_end_to_end(shelf_root: Path, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(shelf_root)
    assert main(["--root", root, "add", str(sample_pdf), "--id", "rm-test", "--type", "reference-manual",
                 "--parts", "STM32H750xB", "--revision", "Rev 1", "--page-offset", "1"]) == 0
    assert (shelf_root / "sample.pdf").exists(), "PDF copied under the root"
    assert (shelf_root / ".shelf" / ".gitignore").read_text() == "*\n"

    assert main(["--root", root, "search", "fifo", "depth"]) == 0
    out = capsys.readouterr().out
    assert "rm-test Rev 1  p. 0" in out  # pdf page 1 − offset 1
    assert "[FIFO]" in out

    assert main(["--root", root, "search", "--part", "rp2350", "fifo"]) == 1

    assert main(["--root", root, "wanted", "add", "es0392", "--title", "Errata", "--why", "hw #9"]) == 0
    assert main(["--root", root, "list"]) == 0
    out = capsys.readouterr().out
    assert "rm-test" in out and "es0392" in out

    assert main(["--root", root, "edit", "rm-test", "--revision", "Rev 2"]) == 0
    assert Manifest.load(shelf_root / "shelf.json").get("rm-test").revision == "Rev 2"

    assert main(["--root", root, "verify"]) == 0
    # An uncatalogued PDF is an orphan.
    shutil.copy(sample_pdf, shelf_root / "stray.pdf")
    assert main(["--root", root, "verify"]) == 1
    assert "ORPHAN" in capsys.readouterr().out

    # Re-adding identical content under a new id is refused.
    assert main(["--root", root, "add", str(shelf_root / "stray.pdf"), "--id", "dup", "--type", "datasheet"]) == 1
