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
    "paper",
    "other",
)

TEXT_LAYERS = ("good", "partial", "poor", "unknown")


def format_surnames(authors: list[str]) -> str:
    """"Smith"; "Smith & Jones"; "Smith et al." for three or more.

    The surname is the last whitespace-separated token of the name as printed
    ("JOHN VANDERKOOY" -> "Vanderkooy"), which is right for the overwhelming
    majority and wrong for compound surnames ("VAN DEN BERG" -> "Berg").
    `shelf edit --author` is the fix when it matters.
    """
    last = [a.split()[-1].title() for a in authors if a.strip()]
    if not last:
        return ""
    if len(last) == 1:
        return last[0]
    if len(last) == 2:
        return f"{last[0]} & {last[1]}"
    return f"{last[0]} et al."


@dataclass
class TocEntry:
    section: str  # "51.4.8", "A.2", or "" for unnumbered headings
    title: str
    page: int  # printed page number
    pdf_page: int = 0  # 1-based PDF index; 0 if unknown
    level: int = 1  # nesting depth in the document's outline


@dataclass
class Document:
    id: str
    file: str
    type: str
    parts: list[str] = field(default_factory=list)
    vendor: str = ""
    title: str = ""
    revision: str = "unknown"
    # Bibliographic identity, for documents that are published rather than
    # revised (type "paper"). A paper has no revision; it has an author list,
    # a year, and a venue, and that is what a citation of it must carry.
    authors: list[str] = field(default_factory=list)
    year: int = 0  # 0 = unknown
    venue: str = ""  # "JAES", "DAFx-20", "ICASSP"
    doi: str = ""
    pages: int = 0
    sha256: str = ""
    # printed page = pdf page index (1-based) - page_offset.
    # None means nobody has checked yet; 0 means checked and equal.
    page_offset: int | None = None
    text_layer: str = "unknown"
    toc: list[TocEntry] = field(default_factory=list)
    notes: str = ""
    source_url: str = ""
    added: str = ""
    # Fields whose values were guessed by `shelf inspect` and not yet
    # confirmed by a human, e.g. ["revision", "page_offset"].
    auto: list[str] = field(default_factory=list)
    # Fields that cannot be filled from any source, mapped to why — a scan with
    # no folio on any page has no printed-page offset, and a manual that
    # identifies itself by date alone has no revision. Distinct from unknown:
    # unknown means nobody has looked, this means someone looked and the answer
    # does not exist. Without it every pass re-reads the same dead ends.
    undoable: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in DOC_TYPES:
            raise ShelfError(f"{self.id}: unknown type {self.type!r}; one of {', '.join(DOC_TYPES)}")
        if self.text_layer not in TEXT_LAYERS:
            raise ShelfError(f"{self.id}: text_layer must be one of {', '.join(TEXT_LAYERS)}")
        for name, why in self.undoable.items():
            if not why.strip():
                raise ShelfError(f"{self.id}: undoable {name!r} needs a reason")
            if name in self.auto:
                raise ShelfError(f"{self.id}: {name!r} is both a guess and undoable")

    def is_undoable(self, name: str) -> bool:
        return name in self.undoable

    @property
    def is_paper(self) -> bool:
        return self.type == "paper"

    @property
    def cite_label(self) -> str:
        """The 'which document, which version' half of a citation.

        For a revised document that is its revision ("Rev 8"). For a paper it
        is the byline — "Vanderkooy & Lipshitz (1978), JAES" — because a paper
        is identified by who wrote it and when, not by a revision that will
        never exist. Empty when nothing is known either way.
        """
        if not self.is_paper:
            return "" if self.revision == "unknown" else self.revision
        names = format_surnames(self.authors)
        parts = []
        if names:
            parts.append(names)
        if self.year:
            parts.append(f"({self.year})" if names else str(self.year))
        label = " ".join(parts)
        if self.venue:
            label = f"{label}, {self.venue}" if label else self.venue
        return label

    def printed_page(self, pdf_page: int) -> int:
        return pdf_page - (self.page_offset or 0)

    @property
    def offset_known(self) -> bool:
        return self.page_offset is not None

    def covers(self, part: str) -> bool:
        needle = part.lower()
        return any(needle in p.lower() for p in self.parts)

    def section(self, ref: str) -> TocEntry | None:
        """Find a TOC entry by section number ("51.4.8", "§51.4.8", "A.2")."""
        key = ref.lstrip("§").strip().rstrip(".")
        for t in self.toc:
            if t.section == key:
                return t
        return None

    def enclosing_section(self, pdf_page: int) -> TocEntry | None:
        """The last TOC entry that starts at or before ``pdf_page``."""
        best: TocEntry | None = None
        for t in self.toc:
            if t.pdf_page and t.pdf_page <= pdf_page:
                best = t
            elif t.pdf_page > pdf_page:
                break
        return best

    def section_span(self, entry: TocEntry) -> tuple[int, int]:
        """PDF page range [first, last] a section occupies: from its own page
        to the page before the next entry of the same or shallower level."""
        first = entry.pdf_page
        last = self.pages
        seen = False
        for t in self.toc:
            if t is entry:
                seen = True
                continue
            if seen and t.pdf_page > first and t.level <= entry.level:
                last = t.pdf_page - 1 if t.pdf_page > first else first
                break
        return first, max(first, last)


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

    # Fields that only some documents have. Writing `"venue": ""` onto ninety
    # datasheets, or `"undoable": {}` onto all of them, is noise in the one file
    # a human is meant to read and edit, so these are omitted when unset;
    # `Document`'s defaults restore them on load.
    _OMIT_WHEN_EMPTY = {"authors": [], "year": 0, "venue": "", "doi": "", "undoable": {}}

    def to_dict(self) -> dict[str, Any]:
        docs = []
        for d in self.documents:
            raw = asdict(d)
            for name, empty in self._OMIT_WHEN_EMPTY.items():
                if raw[name] == empty:
                    del raw[name]
            docs.append(raw)
        return {
            "version": self.version,
            "documents": docs,
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
