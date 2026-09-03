"""The manifest: the human-editable, git-tracked catalog of documents.

``shelf.json`` is the source of truth. Everything in ``.shelf/`` is derived
from it plus the PDFs and can be rebuilt at any time.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shelf import ShelfError

SCHEMA_VERSION = 1

DOC_TYPES = (
    "datasheet",
    "reference-manual",
    "errata",
    "programming-manual",
    "app-note",
    "board-datasheet",
    "schematic",
    "user-manual",
    "standard",
    "other",
)

TEXT_LAYERS = ("good", "partial", "poor", "unknown")


@dataclass
class TocEntry:
    section: str
    title: str
    page: int  # printed page number


@dataclass
class Document:
    id: str
    file: str
    type: str
    parts: list[str] = field(default_factory=list)
    vendor: str = ""
    title: str = ""
    revision: str = "unknown"
    pages: int = 0
    sha256: str = ""
    # printed page = pdf page index (1-based) - page_offset
    page_offset: int = 0
    text_layer: str = "unknown"
    toc: list[TocEntry] = field(default_factory=list)
    notes: str = ""
    source_url: str = ""
    added: str = ""

    def __post_init__(self) -> None:
        if self.type not in DOC_TYPES:
            raise ShelfError(f"{self.id}: unknown type {self.type!r}; one of {', '.join(DOC_TYPES)}")
        if self.text_layer not in TEXT_LAYERS:
            raise ShelfError(f"{self.id}: text_layer must be one of {', '.join(TEXT_LAYERS)}")

    def printed_page(self, pdf_page: int) -> int:
        return pdf_page - self.page_offset

    def covers(self, part: str) -> bool:
        needle = part.lower()
        return any(needle in p.lower() for p in self.parts)


@dataclass
class Wanted:
    id: str
    title: str
    why: str = ""
    source: str = ""


@dataclass
class Manifest:
    version: int = SCHEMA_VERSION
    documents: list[Document] = field(default_factory=list)
    wanted: list[Wanted] = field(default_factory=list)

    # -- persistence ---------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Manifest:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ShelfError(f"manifest not found: {path}") from None
        except json.JSONDecodeError as e:
            raise ShelfError(f"{path}: invalid JSON: {e}") from None
        return cls.from_dict(raw, where=str(path))

    @classmethod
    def from_dict(cls, raw: dict[str, Any], where: str = "manifest") -> Manifest:
        version = raw.get("version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ShelfError(f"{where}: schema version {version} not supported (want {SCHEMA_VERSION})")
        docs = []
        for d in raw.get("documents", []):
            d = dict(d)
            d["toc"] = [TocEntry(**t) for t in d.get("toc", [])]
            try:
                docs.append(Document(**d))
            except TypeError as e:
                raise ShelfError(f"{where}: document {d.get('id', '?')}: {e}") from None
        wanted = []
        for w in raw.get("wanted", []):
            try:
                wanted.append(Wanted(**w))
            except TypeError as e:
                raise ShelfError(f"{where}: wanted {w.get('id', '?')}: {e}") from None
        m = cls(version=version, documents=docs, wanted=wanted)
        m._check_unique()
        return m

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "documents": [asdict(d) for d in self.documents],
            "wanted": [asdict(w) for w in self.wanted],
        }

    def save(self, path: Path) -> None:
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")

    # -- queries -------------------------------------------------------

    def get(self, doc_id: str) -> Document:
        for d in self.documents:
            if d.id == doc_id:
                return d
        raise ShelfError(f"no document with id {doc_id!r}")

    def has(self, doc_id: str) -> bool:
        return any(d.id == doc_id for d in self.documents)

    def by_part(self, part: str) -> list[Document]:
        return [d for d in self.documents if d.covers(part)]

    def by_sha(self, sha256: str) -> Document | None:
        for d in self.documents:
            if d.sha256 == sha256:
                return d
        return None

    # -- mutation ------------------------------------------------------

    def add(self, doc: Document) -> None:
        if self.has(doc.id):
            raise ShelfError(f"document id {doc.id!r} already exists")
        dup = self.by_sha(doc.sha256) if doc.sha256 else None
        if dup is not None:
            raise ShelfError(f"identical file already catalogued as {dup.id!r}")
        self.documents.append(doc)
        self.wanted = [w for w in self.wanted if w.id != doc.id]

    def remove(self, doc_id: str) -> Document:
        doc = self.get(doc_id)
        self.documents.remove(doc)
        return doc

    def add_wanted(self, w: Wanted) -> None:
        if self.has(w.id):
            raise ShelfError(f"{w.id!r} is already in the catalog")
        if any(x.id == w.id for x in self.wanted):
            raise ShelfError(f"{w.id!r} is already wanted")
        self.wanted.append(w)

    def remove_wanted(self, doc_id: str) -> None:
        before = len(self.wanted)
        self.wanted = [w for w in self.wanted if w.id != doc_id]
        if len(self.wanted) == before:
            raise ShelfError(f"{doc_id!r} is not in the wanted list")

    def _check_unique(self) -> None:
        seen: set[str] = set()
        for d in self.documents:
            if d.id in seen:
                raise ShelfError(f"duplicate document id {d.id!r}")
            seen.add(d.id)
        for w in self.wanted:
            if w.id in seen:
                raise ShelfError(f"{w.id!r} is both catalogued and wanted")
            seen.add(w.id)
