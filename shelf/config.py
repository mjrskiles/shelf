"""Locate a shelf root and its derived-data directory."""

from __future__ import annotations

import os
from pathlib import Path

from shelf import ShelfError

MANIFEST_NAME = "shelf.json"
TOC_DIR = "toc"
STATE_DIR = ".shelf"
DB_NAME = "catalog.db"


def find_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the shelf root.

    Precedence: explicit argument, ``SHELF_ROOT`` environment variable, then
    the nearest ancestor of the working directory containing ``shelf.json``.
    """
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not (root / MANIFEST_NAME).is_file():
            raise ShelfError(f"no {MANIFEST_NAME} in {root}")
        return root

    env = os.environ.get("SHELF_ROOT")
    if env:
        return find_root(env)

    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / MANIFEST_NAME).is_file():
            return candidate
    raise ShelfError(
        f"no {MANIFEST_NAME} found in {here} or its parents "
        f"(run `shelf init`, pass --root, or set SHELF_ROOT)"
    )


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_NAME


def toc_dir(root: Path) -> Path:
    """Tables of contents, one ``<id>.json`` per document, next to the
    manifest and tracked with it. A TOC is derived from the PDF outline, but
    it is also what makes a section citable, so it travels with the catalogue
    rather than with the index."""
    return root / TOC_DIR


def toc_path(root: Path, doc_id: str) -> Path:
    return toc_dir(root) / f"{doc_id}.json"


def db_path(root: Path) -> Path:
    return root / STATE_DIR / DB_NAME


def ensure_state_dir(root: Path) -> Path:
    """Create ``.shelf/`` with a self-ignoring .gitignore so derived data never
    lands in the user's repository."""
    state = root / STATE_DIR
    state.mkdir(exist_ok=True)
    ignore = state / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")
    return state
