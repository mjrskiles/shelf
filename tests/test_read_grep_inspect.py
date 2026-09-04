from pathlib import Path

import pytest

from shelf.cli import main
from shelf.inspect import detect_offset, detect_revision
from shelf.manifest import Manifest
from tests.conftest import make_pdf, poppler


def test_detect_offset_from_footer_fractions() -> None:
    total = 6
    pages = [(1, "Cover"), (2, "Contents"), (3, "body 1/6"), (4, "body 2/6"), (5, "body 3/6"), (6, "body 4/6")]
    det = detect_offset(pages, total)
    assert det is not None and det.value == 2
    assert det.evidence == [3, 4, 5, 6]


def test_detect_offset_rejects_disagreement_and_absence() -> None:
    assert detect_offset([(1, "no numbers here"), (2, "none")], 2) is None
    # Fractions whose denominator is not the page count are ignored (ratios in prose).
    assert detect_offset([(1, "duty cycle 1/2"), (2, "gain 3/4"), (3, "x")], 3) is None


def test_detect_offset_ignores_appendix_subnumbering() -> None:
    # TI datasheets: 40 body pages with bare numbers we can't read, then an
    # addendum numbered "Addendum-Page 1..3" on pdf pages 38-40. Three
    # consecutive agreeing pages in one cluster must not become an offset.
    pages = [(i, f"body text {i}") for i in range(1, 38)]
    pages += [(38, "Addendum-Page 1"), (39, "Addendum-Page 2"), (40, "Pack Materials-Page 3")]
    assert detect_offset(pages, 40) is None
    # ...whereas genuine "page N" footers spread across the document are accepted.
    genuine = [(i, f"DS22039D-page {i}") for i in range(1, 41)]
    det = detect_offset(genuine, 40)
    assert det is not None and det.value == 0


def test_detect_revision_prefers_title_then_cover() -> None:
    assert detect_revision("PCM3060 datasheet (Rev. B)", [(1, "")]).value == "Rev. B"
    assert detect_revision("", [(1, "RM0433 Rev 8  1/3353")]).value == "Rev 8"
    assert detect_revision("", [(1, "ES0392 - Rev 15 - September 2025")]).value == "Rev 15"
    assert detect_revision("", [(1, "Daisy Patch Submodule v1.0.5")]).value == "v1.0.5"
    assert detect_revision("", [(1, "SLAS123D – REVISED MARCH 2016")]) is None  # 'REVISED' is not a revision
    assert detect_revision("", [(1, "Rev 2025 catalogue")]) is None  # years are not revisions


@poppler
def test_cli_inspect_read_grep_roundtrip(shelf_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(shelf_root)
    pdf = shelf_root / "widget.pdf"
    pdf.write_bytes(make_pdf([
        "Widget Datasheet Rev. C",
        "Introduction 1/4",
        "The FIFO depth is sixteen 2/4",
        "VTOR reset value is implementation defined 3/4",
    ]))
    assert main(["--root", root, "add", str(pdf), "--id", "widget", "--type", "datasheet"]) == 0
    capsys.readouterr()

    # Dry run reports what it would set; nothing written yet.
    assert main(["--root", root, "inspect"]) == 0
    out = capsys.readouterr().out
    assert "would set page_offset, revision" in out
    assert Manifest.load(shelf_root / "shelf.json").get("widget").revision == "unknown"

    assert main(["--root", root, "inspect", "--apply"]) == 0
    capsys.readouterr()
    doc = Manifest.load(shelf_root / "shelf.json").get("widget")
    assert doc.page_offset == 1 and doc.revision == "Rev. C"
    assert sorted(doc.auto) == ["page_offset", "revision"]

    # verify flags the guesses; edit confirms and clears the marker.
    assert main(["--root", root, "verify"]) == 0
    assert "AUTO     widget: page_offset, revision" in capsys.readouterr().out
    assert main(["--root", root, "edit", "widget", "--revision", "Rev. C"]) == 0
    assert Manifest.load(shelf_root / "shelf.json").get("widget").auto == ["page_offset"]
    capsys.readouterr()

    # read takes printed pages once the offset is known: printed 2 → pdf 3.
    assert main(["--root", root, "read", "widget", "2"]) == 0
    out = capsys.readouterr().out
    assert "widget Rev. C, p. 2 (pdf p. 3)" in out and "FIFO depth" in out
    assert main(["--root", root, "read", "--pdf", "widget", "3"]) == 0
    assert "FIFO depth" in capsys.readouterr().out
    assert main(["--root", root, "read", "widget", "2-3"]) == 0
    out = capsys.readouterr().out
    assert "FIFO depth" in out and "VTOR" in out

    # grep is regex, cites printed pages, and honours --doc.
    assert main(["--root", root, "grep", r"V[T]OR reset", "--doc", "widget"]) == 0
    out = capsys.readouterr().out
    assert "widget Rev. C, p. 3 (pdf p. 4)" in out and "> VTOR reset value" in out
    assert main(["--root", root, "grep", "nonexistent-token"]) == 1
    assert main(["--root", root, "grep", "(", "--doc", "widget"]) == 1  # bad regex → error, not traceback
    capsys.readouterr()
