"""Bulk intake: find PDFs the manifest doesn't know about, guess their
metadata, detect moved and duplicate files, and (optionally) add them.

Everything guessed here is a starting point. ``shelf ingest`` marks each new
record ``revision: unknown`` and notes that it was ingested, so ``shelf
verify`` keeps nagging until a human confirms the metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shelf.index import Index
from shelf.manifest import Document, Manifest
from shelf.pdf import extract_pages, pdf_info, sha256_file, text_layer_quality

PDF_SUFFIXES = {".pdf"}


@dataclass
class Candidate:
    file: str  # relative to root, posix
    sha256: str
    pages: int
    title: str
    id: str
    type: str
    parts: list[str] = field(default_factory=list)


@dataclass
class Plan:
    new: list[Candidate] = field(default_factory=list)
    relocated: list[tuple[Document, str]] = field(default_factory=list)  # (doc, new relative path)
    duplicates: list[tuple[str, str]] = field(default_factory=list)  # (relative path, id it duplicates)

    def is_empty(self) -> bool:
        return not (self.new or self.relocated or self.duplicates)


# -- scanning ---------------------------------------------------------------


def find_pdfs(root: Path, paths: list[Path] | None = None) -> list[Path]:
    starts = paths or [root]
    found: list[Path] = []
    for start in starts:
        if start.is_file():
            if start.suffix.lower() in PDF_SUFFIXES:
                found.append(start)
            continue
        for p in start.rglob("*"):
            if p.is_file() and p.suffix.lower() in PDF_SUFFIXES and ".shelf" not in p.parts:
                found.append(p)
    return sorted(set(found))


def scan(root: Path, manifest: Manifest, paths: list[Path] | None = None) -> Plan:
    plan = Plan()
    by_sha = {d.sha256: d for d in manifest.documents if d.sha256}
    known_files = {d.file for d in manifest.documents}
    # Wanted ids are deliberately not "taken": a new file that guesses to a
    # wanted id fulfils that entry (Manifest.add drops it from wanted).
    taken_ids = {d.id for d in manifest.documents}
    seen_new: dict[str, str] = {}  # sha -> id assigned in this plan

    for pdf in find_pdfs(root, paths):
        rel = pdf.relative_to(root).as_posix()
        if rel in known_files:
            continue
        sha = sha256_file(pdf)

        if sha in by_sha:
            doc = by_sha[sha]
            if (root / doc.file).is_file():
                plan.duplicates.append((rel, doc.id))
            else:
                plan.relocated.append((doc, rel))
            continue
        if sha in seen_new:
            plan.duplicates.append((rel, seen_new[sha]))
            continue

        info = pdf_info(pdf)
        doc_id = unique_id(guess_id(rel, info.title), taken_ids)
        taken_ids.add(doc_id)
        seen_new[sha] = doc_id
        plan.new.append(Candidate(
            file=rel, sha256=sha, pages=info.pages, title=info.title, id=doc_id,
            type=guess_type(rel, info.title), parts=guess_parts(rel, info.title),
        ))
    return plan


# -- applying ---------------------------------------------------------------


def apply(plan: Plan, manifest: Manifest, root: Path, index: Index) -> None:
    for doc, rel in plan.relocated:
        doc.file = rel
    for c in plan.new:
        pages = extract_pages(root / c.file)
        doc = Document(
            id=c.id, file=c.file, type=c.type, parts=c.parts, title=c.title,
            pages=c.pages, sha256=c.sha256, text_layer=text_layer_quality(pages),
            notes="ingested — verify type, parts, revision", added=date.today().isoformat(),
        )
        manifest.add(doc)
        index.index_document(doc, pages)


# -- guessing ---------------------------------------------------------------

# Vendor document numbers: ST (RM0433, ES0392, UM1472, PM0253, AN4891, DS12110),
# TI (SPRUH73Q, SLAS...), NXP (UM10204), ARM (DUI0553, DDI0489).
_DOC_NUMBER = re.compile(r"\b(rm|es|um|pm|an|ds|dui|ddi|spru|slas|slos|sbos)[-_ ]?(\d{3,6}[a-z]?)\b", re.I)

_TYPE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\berrata\b|\bes\d{4}\b", re.I), "errata"),
    (re.compile(r"reference manual|\brefman\b|\brm\d{4}\b|[-_]rm\b|_rm\.|technical reference", re.I), "reference-manual"),
    (re.compile(r"programming manual|\bpm\d{4}\b", re.I), "programming-manual"),
    (re.compile(r"application ?note|app ?note|\ban\d{4}\b|\bcd00\d{6}\b", re.I), "app-note"),
    (re.compile(r"\bschematic|\.sch\b|_sch\b", re.I), "schematic"),
    # Published interface standards, not product specs: MIDI, I2C-bus (UM10204), USB, etc.
    (re.compile(r"\bmidi\b.*(specification|basics)|general midi|i2c-bus specification|\bum10204\b|\busb\b.*specification", re.I), "standard"),
    (re.compile(r"user manual|user[’']?s? guide|getting started|tutorial|\bum\d{4}\b|product manual|\bmanual\b", re.I), "user-manual"),
    (re.compile(r"datasheet|data sheet|\bds-?\d+\b", re.I), "datasheet"),
]

_PAPER_DIRS = re.compile(r"(^|/)(papers?|articles?|aes|dafx|research)(/|$)", re.I)
_DATASHEET_DIRS = re.compile(r"(^|/)(data ?sheets?|ics?|components?|parts)(/|$)", re.I)


def guess_type(rel: str, title: str) -> str:
    if _PAPER_DIRS.search(rel.rsplit("/", 1)[0] if "/" in rel else ""):
        return "paper"
    haystack = f"{Path(rel).stem} {title}"
    for pattern, doc_type in _TYPE_RULES:
        if pattern.search(haystack):
            return doc_type
    if _DATASHEET_DIRS.search(rel):
        return "datasheet"
    return "other"


def guess_id(rel: str, title: str) -> str:
    stem = Path(rel).stem
    m = _DOC_NUMBER.search(stem) or _DOC_NUMBER.search(title)
    if m:
        return f"{m.group(1)}{m.group(2)}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    slug = re.sub(r"-\d{6,}$", "", slug)  # trailing distributor order numbers
    slug = re.sub(r"-rev-?\d+(-\d+)*$", "", slug)  # _rev2, _rev0_1
    slug = re.sub(r"-(19|20)\d{2}$", "", slug)  # _2009
    return slug[:48] or "document"


def unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# Part-number-shaped tokens: 1–4 letters, 2–6 digits, optional suffix.
_PART = re.compile(r"\b([A-Z]{1,5}\d{2,6}[A-Z0-9]{0,6})\b")
_NOT_PARTS = re.compile(
    r"^(rm|es|um|pm|an|ds|dui|ddi|spru|slas|slos|sbos|rev|ver|spec|v|com|cd00|iso|ieee|rp|xx)\d*",
    re.I,
)


def guess_parts(rel: str, title: str) -> list[str]:
    text = f"{Path(rel).stem} {title}".upper().replace("_", " ")
    parts: list[str] = []
    for tok in _PART.findall(text):
        if _NOT_PARTS.match(tok) or tok.isdigit() or re.fullmatch(r"\d{4}[A-Z]?", tok):
            continue
        if tok not in parts:
            parts.append(tok)
    return parts[:6]
