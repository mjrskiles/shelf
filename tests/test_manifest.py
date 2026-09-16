from pathlib import Path

import pytest

from shelf import ShelfError
from shelf.manifest import SCHEMA_VERSION, Document, Manifest, TocEntry, Wanted


def _doc(**kw) -> Document:
    base = dict(id="rm0433", file="rm0433.pdf", type="reference-manual", parts=["STM32H750xB"], sha256="abc")
    base.update(kw)
    return Document(**base)


def test_roundtrip(tmp_path: Path) -> None:
    m = Manifest()
    d = _doc(revision="Rev 8", pages=3353, page_offset=0, toc=[TocEntry("51.5.8", "FIFO", 2048)])
    m.add(d)
    m.add_wanted(Wanted("es0392", "Errata", why="hw #9"))
    path = tmp_path / "shelf.json"
    m.save(path)

    loaded = Manifest.load(path)
    assert loaded.get("rm0433").toc[0].page == 2048
    assert loaded.wanted[0].why == "hw #9"
    assert path.read_text().endswith("}\n")


def test_toc_lives_in_a_sidecar(tmp_path: Path) -> None:
    m = Manifest()
    m.add(_doc(toc=[TocEntry("1", "Intro", 1, pdf_page=3), TocEntry("2", "Regs", 8, pdf_page=10)]))
    path = tmp_path / "shelf.json"
    m.save(path)

    assert "toc" not in path.read_text()
    sidecar = tmp_path / "toc" / "rm0433.json"
    text = sidecar.read_text()
    assert text.startswith('{\n  "id": "rm0433",')
    assert text.count('"section"') == 2 and text.count("\n") == 7  # one entry per line
    assert text.endswith("\n")
    assert [t.title for t in Manifest.load(path).get("rm0433").toc] == ["Intro", "Regs"]

    # An unrelated save leaves the sidecar untouched; an empty TOC removes it.
    before = sidecar.stat().st_mtime_ns
    m.get("rm0433").revision = "Rev 9"
    m.save(path)
    assert sidecar.stat().st_mtime_ns == before
    m.get("rm0433").toc = []
    m.save(path)
    assert not sidecar.exists()
    assert Manifest.load(path).get("rm0433").toc == []


def test_orphan_and_malformed_sidecars(tmp_path: Path) -> None:
    m = Manifest()
    m.add(_doc())
    path = tmp_path / "shelf.json"
    m.save(path)
    (tmp_path / "toc").mkdir()
    stray = tmp_path / "toc" / "gone.json"
    stray.write_text('{"id": "gone", "entries": []}\n')
    assert m.orphan_tocs(path) == [stray]

    (tmp_path / "toc" / "rm0433.json").write_text('{"entries": [{"nope": 1}]}\n')
    with pytest.raises(ShelfError, match="malformed TOC"):
        Manifest.load(path)


def test_inline_toc_schema_rejected(tmp_path: Path) -> None:
    path = tmp_path / "shelf.json"
    path.write_text('{"version": 1, "documents": [], "wanted": []}\n')
    with pytest.raises(ShelfError, match="schema version 1"):
        Manifest.load(path)


def test_add_removes_from_wanted() -> None:
    m = Manifest()
    m.add_wanted(Wanted("rm0433", "RM"))
    m.add(_doc())
    assert m.wanted == []


def test_duplicate_id_rejected() -> None:
    m = Manifest()
    m.add(_doc())
    with pytest.raises(ShelfError, match="already exists"):
        m.add(_doc(file="other.pdf", sha256="def"))


def test_duplicate_content_rejected() -> None:
    m = Manifest()
    m.add(_doc())
    with pytest.raises(ShelfError, match="identical file"):
        m.add(_doc(id="rm0433-copy", file="copy.pdf"))


def test_unknown_type_rejected() -> None:
    with pytest.raises(ShelfError, match="unknown type"):
        _doc(type="brochure")


def test_by_part_is_case_insensitive_substring() -> None:
    m = Manifest()
    m.add(_doc())
    assert m.by_part("stm32h7") == [m.get("rm0433")]
    assert m.by_part("rp2350") == []


def test_printed_page_offset() -> None:
    d = _doc(page_offset=12)
    assert d.printed_page(20) == 8


def test_wanted_and_catalogued_conflict_detected() -> None:
    raw = {
        "version": SCHEMA_VERSION,
        "documents": [{"id": "x", "file": "x.pdf", "type": "datasheet"}],
        "wanted": [{"id": "x", "title": "X"}],
    }
    with pytest.raises(ShelfError, match="both catalogued and wanted"):
        Manifest.from_dict(raw)
