"""sync against rclone's local backend — a plain directory is a valid remote."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from shelf.cli import main
from shelf.manifest import Manifest
from tests.conftest import make_pdf, poppler

rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def _clone_catalogue(src: Path, dst: Path) -> None:
    """What git gives a second machine: the manifest and the TOCs, no PDFs."""
    dst.mkdir()
    shutil.copy(src / "shelf.json", dst / "shelf.json")
    if (src / "toc").is_dir():
        shutil.copytree(src / "toc", dst / "toc")


@rclone
@poppler
def test_push_pull_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "store"
    a = tmp_path / "a"
    a.mkdir()
    ra = str(a)
    assert main(["--root", ra, "init", ra]) == 0
    (a / "rm.pdf").write_bytes(make_pdf(["FIFO depth is sixteen", "page two"]))
    (a / "papers").mkdir()
    (a / "papers" / "p.pdf").write_bytes(make_pdf(["a paper about bows"]))
    assert main(["--root", ra, "add", str(a / "rm.pdf"), "--id", "rm", "--type", "reference-manual"]) == 0
    assert main(["--root", ra, "add", str(a / "papers" / "p.pdf"), "--id", "p", "--type", "paper"]) == 0
    capsys.readouterr()

    assert main(["--root", ra, "sync", "status"]) == 1  # no remote yet
    assert "no remote" in capsys.readouterr().err
    assert main(["--root", ra, "sync", "remote", str(store)]) == 0
    assert Manifest.load(a / "shelf.json").remote == str(store)

    assert main(["--root", ra, "sync", "status"]) == 0
    assert "2 to push" in capsys.readouterr().out
    assert main(["--root", ra, "sync", "push"]) == 0
    out = capsys.readouterr().out
    assert "pushed 2 blob(s) and the index" in out
    sha = Manifest.load(a / "shelf.json").get("rm").sha256
    assert (store / "blobs" / sha).is_file()
    assert (store / "index" / "catalog.db").is_file()
    assert main(["--root", ra, "sync", "push"]) == 0  # idempotent
    assert "pushed 0 blob(s)" in capsys.readouterr().out

    # Machine B has the catalogue from git and nothing else.
    b = tmp_path / "b"
    _clone_catalogue(a, b)
    rb = str(b)
    assert main(["--root", rb, "sync", "status"]) == 0
    assert "2 to pull" in capsys.readouterr().out
    assert main(["--root", rb, "sync", "pull", "--index-only"]) == 0
    assert "index arrived" in capsys.readouterr().out
    assert not (b / "rm.pdf").exists()
    assert main(["--root", rb, "search", "sixteen"]) == 0  # search with no PDFs at all
    assert "rm" in capsys.readouterr().out

    assert main(["--root", rb, "sync", "pull"]) == 0
    assert "pulled 2 blob(s)" in capsys.readouterr().out
    assert (b / "rm.pdf").read_bytes() == (a / "rm.pdf").read_bytes()
    assert (b / "papers" / "p.pdf").is_file()  # placed under its catalogued path
    assert not list((b / ".shelf").glob("sync-*"))  # staging cleaned up
    assert main(["--root", rb, "verify"]) == 0


@rclone
@poppler
def test_pull_rejects_a_blob_that_does_not_match(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "store"
    a = tmp_path / "a"
    a.mkdir()
    ra = str(a)
    assert main(["--root", ra, "init", ra]) == 0
    (a / "rm.pdf").write_bytes(make_pdf(["genuine"]))
    assert main(["--root", ra, "add", str(a / "rm.pdf"), "--id", "rm", "--type", "datasheet"]) == 0
    assert main(["--root", ra, "sync", "remote", str(store)]) == 0
    assert main(["--root", ra, "sync", "push", "--no-index"]) == 0
    sha = Manifest.load(a / "shelf.json").get("rm").sha256
    (store / "blobs" / sha).write_bytes(make_pdf(["tampered"]))

    b = tmp_path / "b"
    _clone_catalogue(a, b)
    capsys.readouterr()
    assert main(["--root", str(b), "sync", "pull"]) == 1
    out = capsys.readouterr().out
    assert "REJECTED rm" in out and "does not match" in out
    assert not (b / "rm.pdf").exists()
