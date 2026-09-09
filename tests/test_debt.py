from pathlib import Path

import pytest

from shelf import ShelfError
from shelf.debt import census, gaps_for, total_open
from shelf.manifest import Document, Manifest


def _doc(**kw) -> Document:
    base = dict(id="d1", file="d1.pdf", type="datasheet", sha256="abc")
    base.update(kw)
    return Document(**base)


def _fields(doc: Document, tier: str | None = None) -> set[str]:
    return {g.field for g in gaps_for(doc) if tier is None or g.tier == tier}


# -- tiers -------------------------------------------------------------------


def test_bare_document_owes_every_field() -> None:
    assert _fields(_doc()) == {"revision", "title", "page_offset", "source_url"}


def test_complete_document_owes_nothing() -> None:
    d = _doc(revision="Rev. C", title="T", page_offset=2, source_url="http://ti.com/x")
    assert gaps_for(d) == []


def test_paper_owes_byline_not_revision() -> None:
    d = _doc(type="paper", title="T", page_offset=0, source_url="u")
    assert _fields(d) == {"authors", "year"}
    assert _fields(d, "cover") == {"authors"}
    assert _fields(d, "web") == {"year"}


def test_guess_is_confirm_tier_not_its_own_tier() -> None:
    """A guessed offset needs corroborating, not deriving from scratch."""
    d = _doc(revision="R", title="T", source_url="u", page_offset=0, auto=["page_offset"])
    assert _fields(d, "confirm") == {"page_offset"}
    assert _fields(d, "offset") == set()


def test_guessed_revision_is_not_also_a_cover_gap() -> None:
    d = _doc(revision="Rev. C", title="T", page_offset=0, source_url="u", auto=["revision"])
    assert [g.tier for g in gaps_for(d)] == ["confirm"]


# -- undoable ----------------------------------------------------------------


def test_undoable_field_is_not_owed() -> None:
    d = _doc(revision="R", title="T", source_url="u", undoable={"page_offset": "no folio"})
    assert gaps_for(d) == []


def test_undoable_needs_a_reason() -> None:
    with pytest.raises(ShelfError, match="needs a reason"):
        _doc(undoable={"page_offset": "  "})


def test_field_cannot_be_both_guessed_and_undoable() -> None:
    with pytest.raises(ShelfError, match="both a guess and undoable"):
        _doc(page_offset=0, auto=["page_offset"], undoable={"page_offset": "no folio"})


def test_undoable_survives_a_roundtrip_and_is_omitted_when_empty(tmp_path: Path) -> None:
    m = Manifest()
    m.add(_doc(undoable={"revision": "identifies itself by date only"}))
    m.add(_doc(id="d2", file="d2.pdf", sha256="def"))
    path = tmp_path / "shelf.json"
    m.save(path)

    assert '"undoable"' in path.read_text()
    assert path.read_text().count('"undoable"') == 1  # d2 has none, so writes none
    assert Manifest.load(path).get("d1").undoable == {"revision": "identifies itself by date only"}


# -- census ------------------------------------------------------------------


def _manifest(n: int) -> Manifest:
    m = Manifest()
    for i in range(n):
        m.add(_doc(id=f"d{i:02d}", file=f"d{i:02d}.pdf", sha256=f"sha{i}"))
    return m


def test_census_limit_caps_documents_and_is_stable() -> None:
    m = _manifest(10)
    first = census(m, tier="cover", limit=3)["cover"]
    assert {g.doc_id for g in first} == {"d00", "d01", "d02"}
    assert first == census(m, tier="cover", limit=3)["cover"]


def test_census_limit_does_not_change_the_total() -> None:
    m = _manifest(10)
    assert total_open(m) == 40  # four owed fields each
    assert len(census(m, limit=2)["cover"]) == 4  # two docs, two cover fields each


def test_census_tier_selects_one_tier() -> None:
    assert set(census(_manifest(2), tier="web")) == {"web"}


# -- cli ---------------------------------------------------------------------


def test_cli_undoable_roundtrip(shelf_root: Path, sample_pdf: Path, capsys) -> None:
    from shelf.cli import main

    root = str(shelf_root)
    assert main(["--root", root, "add", str(sample_pdf), "--id", "w", "--type", "datasheet"]) == 0
    capsys.readouterr()

    def record():
        return Manifest.load(shelf_root / "shelf.json").get("w")

    # A reason is not optional — "cannot be filled" is a finding, not a shrug.
    assert main(["--root", root, "undoable", "w", "page_offset"]) == 1
    assert "reason is required" in capsys.readouterr().err

    assert main(["--root", root, "undoable", "w", "page_offset", "no folio on any page"]) == 0
    capsys.readouterr()
    assert record().undoable == {"page_offset": "no folio on any page"}

    # Retired fields leave the census and show up as retired in the total.
    assert main(["--root", root, "debt"]) == 0
    out = capsys.readouterr().out
    assert "page_offset" not in out
    assert "1 retired as undoable" in out

    # `inspect --apply` must not re-guess a retired field, even with --force.
    assert main(["--root", root, "inspect", "--apply", "--force"]) == 0
    capsys.readouterr()
    assert record().page_offset is None

    # show says so rather than claiming nobody has checked.
    assert main(["--root", root, "show", "w"]) == 0
    assert "no page offset — undoable" in capsys.readouterr().out

    # Filling the field by hand retires the retirement.
    assert main(["--root", root, "edit", "w", "--page-offset", "1"]) == 0
    capsys.readouterr()
    assert record().undoable == {} and record().page_offset == 1

    assert main(["--root", root, "undoable", "w", "revision", "date only"]) == 0
    assert main(["--root", root, "undoable", "w", "revision", "--clear"]) == 0
    assert "open again" in capsys.readouterr().out
    assert record().undoable == {}


def test_cli_undoable_rejects_an_uncuratable_field(shelf_root: Path, sample_pdf: Path, capsys) -> None:
    from shelf.cli import main

    root = str(shelf_root)
    assert main(["--root", root, "add", str(sample_pdf), "--id", "w", "--type", "datasheet"]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["--root", root, "undoable", "w", "sha256", "nope"])
