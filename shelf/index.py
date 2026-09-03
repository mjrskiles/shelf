"""The derived full-text index: SQLite + FTS5 over per-page text.

Rebuildable from the manifest and the PDFs at any time; never the source of
truth for anything.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from shelf import ShelfError
from shelf.manifest import Document, Manifest
from shelf.pdf import extract_pages

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id      TEXT PRIMARY KEY,
    sha256  TEXT NOT NULL,
    pages   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS doc_parts (
    doc_id  TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    part    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_parts_part ON doc_parts(part);
CREATE TABLE IF NOT EXISTS pages (
    id        INTEGER PRIMARY KEY,
    doc_id    TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    pdf_page  INTEGER NOT NULL,
    text      TEXT NOT NULL,
    UNIQUE(doc_id, pdf_page)
);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    text,
    content='pages',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""


@dataclass
class Hit:
    doc_id: str
    pdf_page: int
    printed_page: int
    snippet: str
    rank: float


@dataclass
class BuildStats:
    indexed: list[str]
    skipped: list[str]
    missing: list[str]


class Index:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- building ------------------------------------------------------

    def build(self, manifest: Manifest, root: Path, force: bool = False) -> BuildStats:
        stats = BuildStats(indexed=[], skipped=[], missing=[])
        for doc in manifest.documents:
            pdf = root / doc.file
            if not pdf.is_file():
                stats.missing.append(doc.id)
                continue
            if not force and self._current(doc):
                stats.skipped.append(doc.id)
                continue
            self.index_document(doc, extract_pages(pdf))
            stats.indexed.append(doc.id)
        self._drop_orphans({d.id for d in manifest.documents})
        self._rebuild_fts()
        return stats

    def index_document(self, doc: Document, pages: Iterable[str]) -> None:
        """Replace whatever is indexed for ``doc`` with ``pages``.

        Callers that already extracted the text (e.g. ``shelf add``) use this
        directly; ``build`` calls it after extracting.
        """
        if not doc.sha256:
            raise ShelfError(f"{doc.id}: cannot index a document without a sha256")
        c = self._conn
        with c:
            c.execute("DELETE FROM docs WHERE id = ?", (doc.id,))
            c.execute("INSERT INTO docs(id, sha256, pages) VALUES (?, ?, ?)", (doc.id, doc.sha256, doc.pages))
            c.executemany("INSERT INTO doc_parts(doc_id, part) VALUES (?, ?)",
                          [(doc.id, p.lower()) for p in doc.parts])
            c.executemany(
                "INSERT INTO pages(doc_id, pdf_page, text) VALUES (?, ?, ?)",
                [(doc.id, i + 1, text) for i, text in enumerate(pages)],
            )
        self._rebuild_fts()

    def _current(self, doc: Document) -> bool:
        row = self._conn.execute("SELECT sha256 FROM docs WHERE id = ?", (doc.id,)).fetchone()
        return row is not None and row[0] == doc.sha256

    def _drop_orphans(self, live_ids: set[str]) -> None:
        with self._conn:
            for (doc_id,) in self._conn.execute("SELECT id FROM docs").fetchall():
                if doc_id not in live_ids:
                    self._conn.execute("DELETE FROM docs WHERE id = ?", (doc_id,))

    def _rebuild_fts(self) -> None:
        with self._conn:
            self._conn.execute("INSERT INTO pages_fts(pages_fts) VALUES ('rebuild')")

    # -- searching -----------------------------------------------------

    def search(
        self,
        query: str,
        manifest: Manifest,
        part: str | None = None,
        doc_id: str | None = None,
        limit: int = 20,
        raw: bool = False,
    ) -> list[Hit]:
        fts_query = query if raw else _phrase_query(query)
        sql = """
            SELECT p.doc_id, p.pdf_page,
                   snippet(pages_fts, 0, '[', ']', ' … ', 14) AS snip,
                   bm25(pages_fts) AS rank
            FROM pages_fts
            JOIN pages p ON p.id = pages_fts.rowid
            WHERE pages_fts MATCH ?
        """
        params: list[object] = [fts_query]
        if doc_id:
            sql += " AND p.doc_id = ?"
            params.append(doc_id)
        if part:
            sql += " AND p.doc_id IN (SELECT doc_id FROM doc_parts WHERE part LIKE ?)"
            params.append(f"%{part.lower()}%")
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            raise ShelfError(f"search failed: {e} (query was {fts_query!r})") from None
        hits = []
        for did, pdf_page, snip, rank in rows:
            printed = manifest.get(did).printed_page(pdf_page) if manifest.has(did) else pdf_page
            hits.append(Hit(did, pdf_page, printed, " ".join(snip.split()), rank))
        return hits

    def indexed_ids(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT id FROM docs ORDER BY id").fetchall()]


def _phrase_query(query: str) -> str:
    """Turn free text into an FTS5 query: each token becomes a quoted phrase,
    implicitly ANDed. Quotes inside tokens are escaped by doubling."""
    tokens = [t for t in query.split() if t]
    if not tokens:
        raise ShelfError("empty search query")
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)
