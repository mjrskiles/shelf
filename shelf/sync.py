"""Replicate the corpus: blobs to a remote store, content-addressed by sha256.

Hosting is three problems, not one. The manifest is text and lives in git.
The PDFs are vendor copyright and live in one private store, named by their
hash so a file renamed or moved locally is never uploaded twice and two
catalogues of the same document share one blob. The index is derived, but a
machine that pulls only the index gets full search and page text without a
single PDF, so it is pushed alongside as a build artifact.

Every transfer goes through rclone, so the store is whatever rclone can
reach: an Azure container, an S3 bucket, an SFTP host, or a plain directory
(which is what the tests use). Layout under the remote path::

    blobs/<sha256>        one per document, immutable
    index/catalog.db      the FTS index, newest wins
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from shelf import ShelfError
from shelf.config import STATE_DIR, db_path
from shelf.manifest import Document, Manifest
from shelf.pdf import sha256_file

BLOBS = "blobs"
INDEX = "index/catalog.db"


def require_rclone() -> None:
    if shutil.which("rclone") is None:
        raise ShelfError("rclone not found (https://rclone.org/install/)")


def resolve_remote(explicit: str | None, manifest: Manifest) -> str:
    """Precedence: --remote, SHELF_REMOTE, the manifest's recorded remote."""
    remote = explicit or os.environ.get("SHELF_REMOTE") or manifest.remote
    if not remote:
        raise ShelfError(
            "no remote configured (run `shelf sync remote <rclone-path>`, pass --remote, "
            "or set SHELF_REMOTE)"
        )
    return remote.rstrip("/")


def _rclone(args: list[str], verbose: bool = False, ok_codes: tuple[int, ...] = (0,)) -> str:
    """Run rclone, passing progress through to stderr; return stdout."""
    require_rclone()
    cmd = ["rclone", *args]
    if verbose:
        cmd.append("-v")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=None if verbose else subprocess.PIPE,
                          text=True, errors="replace")
    if proc.returncode not in ok_codes:
        err = (proc.stderr or "").strip().splitlines()
        tail = err[-1] if err else f"exit {proc.returncode}"
        raise ShelfError(f"rclone {args[0]} failed: {tail}")
    # A tolerated failure (a path that does not exist yet) leaves partial
    # output behind; the caller asked to treat it as nothing.
    return proc.stdout if proc.returncode == 0 else ""


def listing(remote: str, verbose: bool = False) -> dict[str, int]:
    """Every file under the remote, as ``{relative path: size}``.

    A remote that does not exist yet lists as empty rather than failing —
    the first push creates it.
    """
    out = _rclone(["lsjson", "--files-only", "-R", remote], verbose, ok_codes=(0, 3))
    if not out.strip():
        return {}
    return {e["Path"]: int(e["Size"]) for e in json.loads(out)}


@dataclass
class Plan:
    push: list[Document] = field(default_factory=list)      # here, not there
    pull: list[Document] = field(default_factory=list)      # there, not here
    both: list[Document] = field(default_factory=list)
    nowhere: list[Document] = field(default_factory=list)   # in the catalogue, on no disk
    unhashed: list[Document] = field(default_factory=list)  # no sha256, cannot be addressed
    local_index: bool = False
    remote_index: bool = False


def plan(manifest: Manifest, root: Path, remote_files: dict[str, int]) -> Plan:
    p = Plan(local_index=db_path(root).is_file(), remote_index=INDEX in remote_files)
    for d in manifest.documents:
        if not d.sha256:
            p.unhashed.append(d)
            continue
        here = (root / d.file).is_file()
        there = f"{BLOBS}/{d.sha256}" in remote_files
        bucket = p.both if here and there else p.push if here else p.pull if there else p.nowhere
        bucket.append(d)
    return p


def _staging(root: Path) -> Path:
    state = root / STATE_DIR
    state.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="sync-", dir=state))


def push(manifest: Manifest, root: Path, remote: str, index: bool = True,
         verbose: bool = False) -> Plan:
    """Upload every local document the store lacks, then the index."""
    p = plan(manifest, root, listing(remote, verbose))
    if p.push:
        # A directory of symlinks named by hash lets one rclone call upload
        # everything in parallel under the content-addressed names.
        stage = _staging(root)
        try:
            for d in p.push:
                (stage / d.sha256).symlink_to((root / d.file).resolve())
            _rclone(["copy", "--copy-links", "--ignore-existing", *_stats(), str(stage),
                     f"{remote}/{BLOBS}"], verbose)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    if index and p.local_index:
        _rclone(["copyto", "--update", *_stats(), str(db_path(root)), f"{remote}/{INDEX}"], verbose)
    return p


def pull(manifest: Manifest, root: Path, remote: str, index: bool = True,
         index_only: bool = False, verbose: bool = False) -> tuple[Plan, list[str]]:
    """Download every catalogued document missing here, verified against its
    recorded sha256 before it is placed. Returns the plan and any blobs that
    failed verification (left out of the corpus)."""
    p = plan(manifest, root, listing(remote, verbose))
    bad: list[str] = []
    if p.pull and not index_only:
        stage = _staging(root)
        try:
            wanted = stage / "files.txt"
            wanted.write_text("".join(f"{d.sha256}\n" for d in p.pull))
            _rclone(["copy", "--files-from", str(wanted), *_stats(), f"{remote}/{BLOBS}", str(stage)],
                    verbose)
            for d in p.pull:
                blob = stage / d.sha256
                if not blob.is_file():
                    bad.append(f"{d.id}: blob {d.sha256[:12]}… was not delivered")
                    continue
                if sha256_file(blob) != d.sha256:
                    bad.append(f"{d.id}: blob {d.sha256[:12]}… does not match the recorded sha256")
                    continue
                dest = root / d.file
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(blob, dest)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    if (index or index_only) and p.remote_index:
        db_path(root).parent.mkdir(exist_ok=True)
        _rclone(["copyto", "--update", *_stats(), f"{remote}/{INDEX}", str(db_path(root))], verbose)
    return p, bad


def _stats() -> list[str]:
    # One progress line every few seconds on stderr; a 600 MB push is not
    # silent, and a 600 KB one does not print at all.
    if not sys.stderr.isatty():
        return []
    return ["--stats", "5s", "--stats-one-line", "--stats-log-level", "NOTICE"]
