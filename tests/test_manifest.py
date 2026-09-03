from pathlib import Path

import pytest

from shelf import ShelfError
from shelf.manifest import Document, Manifest, TocEntry, Wanted


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
        "version": 1,
        "documents": [{"id": "x", "file": "x.pdf", "type": "datasheet"}],
        "wanted": [{"id": "x", "title": "X"}],
    }
    with pytest.raises(ShelfError, match="both catalogued and wanted"):
        Manifest.from_dict(raw)
