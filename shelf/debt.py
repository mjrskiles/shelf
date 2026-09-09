"""Metadata debt: which catalog fields are open, and what could close them.

`verify` answers "is the corpus intact?" — files present, hashes matching, no
stray PDFs. This answers a different question: "is the metadata complete?" An
unknown revision or a null page offset is not a broken corpus, it is an unpaid
one, and the two want separate reports and separate exit codes.

Gaps are grouped by the cheapest thing that can close them, because that is
what decides who does the work:

    confirm   a value exists but `inspect` guessed it; corroborate on a page
              the detector did not vote on, then `edit` to clear the flag
    cover     stated in the document's own front matter
    offset    printed folio minus PDF index, from two widely separated pages
    web       not in the document at all — source_url, and years no cover gives

A field recorded as undoable is not counted. Some fields cannot be filled from
any source: a scan with no folio anywhere has no printed-page offset, and
re-reading it every pass is waste. "Unknown" and "cannot be known" are
different answers and the catalog records both.
"""

from __future__ import annotations

from dataclasses import dataclass

from shelf.manifest import Document, Manifest

TIERS = ("confirm", "cover", "offset", "web")

# Fields `undoable` accepts. Everything a curator can be asked to settle.
DEBT_FIELDS = ("revision", "title", "authors", "year", "page_offset", "source_url")


@dataclass(frozen=True)
class Gap:
    doc_id: str
    tier: str
    field: str
    detail: str

    def __lt__(self, other: Gap) -> bool:
        return (self.doc_id, self.field) < (other.doc_id, other.field)


def gaps_for(doc: Document) -> list[Gap]:
    """Every open gap on one document, in no particular order."""
    out: list[Gap] = []

    def add(tier: str, name: str, detail: str) -> None:
        if not doc.is_undoable(name):
            out.append(Gap(doc.id, tier, name, detail))

    for name in doc.auto:
        value = getattr(doc, name, None)
        out.append(Gap(doc.id, "confirm", name, f"guessed {value!r} — corroborate and clear"))

    if doc.is_paper:
        if not doc.authors:
            add("cover", "authors", "byline — believe a name only if the line carries an initial")
        if not doc.year:
            add("web", "year", "no cover date; DOI or venue lookup")
    elif doc.revision == "unknown":
        add("cover", "revision", "cover, cover footer, or revision-history table")

    if not doc.title:
        add("cover", "title", "title block on the cover, verbatim")

    if doc.page_offset is None and "page_offset" not in doc.auto:
        add("offset", "page_offset", "printed folio minus PDF index, on two distant pages")

    if not doc.source_url:
        add("web", "source_url", "vendor or publisher download page")

    return out


def census(manifest: Manifest, tier: str | None = None, limit: int | None = None) -> dict[str, list[Gap]]:
    """Open gaps by tier.

    Sorted by document id, and ``limit`` caps documents rather than gaps, so
    the same ``--limit N`` hands out the same N documents on every run — a
    batch has to be reproducible to be worth planning around.
    """
    wanted = (tier,) if tier else TIERS
    by_tier: dict[str, list[Gap]] = {t: [] for t in wanted}
    for doc in manifest.documents:
        for gap in gaps_for(doc):
            if gap.tier in by_tier:
                by_tier[gap.tier].append(gap)
    for rows in by_tier.values():
        rows.sort()
    if limit is not None:
        for name, rows in by_tier.items():
            keep = sorted({g.doc_id for g in rows})[:limit]
            by_tier[name] = [g for g in rows if g.doc_id in set(keep)]
    return by_tier


def total_open(manifest: Manifest) -> int:
    return sum(len(gaps_for(d)) for d in manifest.documents)
