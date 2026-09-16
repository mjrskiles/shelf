"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from shelf import ShelfError, __version__
from shelf import debt as debt_mod
from shelf import inspect as inspection
from shelf import toc as toc_mod
from shelf.config import (
    MANIFEST_NAME,
    db_path,
    ensure_state_dir,
    find_root,
    manifest_path,
)
from shelf.index import Index
from shelf.ingest import apply as ingest_apply
from shelf.ingest import scan as ingest_scan
from shelf.manifest import DOC_TYPES, Document, Manifest, Wanted, format_surnames
from shelf.pdf import extract_pages, pdf_info, sha256_file, text_layer_quality


# -- helpers -----------------------------------------------------------------


def _load(args: argparse.Namespace) -> tuple[Path, Manifest]:
    root = find_root(args.root)
    return root, Manifest.load(manifest_path(root))


def _open_index(root: Path) -> Index:
    ensure_state_dir(root)
    return Index(db_path(root))


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = [len(h) for h in header]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*header), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*r) for r in rows]
    return "\n".join(line.rstrip() for line in lines)


# -- commands ----------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = manifest_path(root)
    if path.exists():
        raise ShelfError(f"{path} already exists")
    Manifest().save(path)
    ensure_state_dir(root)
    print(f"initialised shelf at {root}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    src = Path(args.pdf).expanduser().resolve()
    if not src.is_file():
        raise ShelfError(f"not a file: {src}")

    sha = sha256_file(src)
    dup = manifest.by_sha(sha)
    if dup is not None:
        raise ShelfError(f"identical file already catalogued as {dup.id!r} ({dup.file})")

    info = pdf_info(src)
    pages = extract_pages(src)

    # Bring the file under the root unless it already lives there.
    try:
        rel = src.relative_to(root)
        dest = src
    except ValueError:
        dest = root / (args.filename or src.name)
        if dest.exists() and sha256_file(dest) != sha:
            raise ShelfError(f"{dest} exists with different content; pass --filename")
        if not dest.exists():
            if args.move:
                shutil.move(str(src), dest)
            else:
                shutil.copy2(src, dest)
        rel = dest.relative_to(root)

    doc = Document(
        id=args.id,
        file=rel.as_posix(),
        type=args.type,
        parts=args.parts or [],
        vendor=args.vendor or "",
        title=args.title or info.title,
        revision=args.revision or "unknown",
        authors=args.author or [],
        year=args.year or 0,
        venue=args.venue or "",
        doi=args.doi or "",
        pages=info.pages,
        sha256=sha,
        page_offset=args.page_offset,
        text_layer=text_layer_quality(pages),
        notes=args.notes or "",
        source_url=args.source or "",
        added=date.today().isoformat(),
    )
    manifest.add(doc)
    manifest.save(manifest_path(root))

    idx = _open_index(root)
    try:
        idx.index_document(doc, pages)
    finally:
        idx.close()

    print(f"added {doc.id}: {doc.file} ({doc.pages} pages, text layer {doc.text_layer})")
    if doc.is_paper and not (doc.authors and doc.year):
        print("  byline incomplete — check the cover and `shelf edit --author/--year` it", file=sys.stderr)
    elif not doc.is_paper and doc.revision == "unknown":
        print("  revision unknown — check the cover and `shelf edit` it", file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    rows = []
    for d in sorted(manifest.documents, key=lambda d: d.id):
        present = "yes" if (root / d.file).is_file() else "NO"
        rows.append([d.id, d.type, ", ".join(d.parts), d.revision, str(d.pages), present])
    if rows:
        print(_table(rows, ["id", "type", "parts", "rev", "pages", "on disk"]))
    else:
        print("no documents catalogued")
    if manifest.wanted and not args.no_wanted:
        print(f"\nwanted ({len(manifest.wanted)}):")
        for w in manifest.wanted:
            print(f"  {w.id:<20} {w.title}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    d = manifest.get(args.id)
    on_disk = (root / d.file).is_file()
    print(f"{d.id}  [{d.type}]  {d.title}")
    print(f"  vendor:      {d.vendor}")
    print(f"  parts:       {', '.join(d.parts)}")
    auto = f"  (auto-detected: {', '.join(d.auto)})" if d.auto else ""
    if d.is_paper:
        print(f"  authors:     {', '.join(d.authors) or 'unknown'}{auto}")
        print(f"  year:        {d.year or 'unknown'}")
        print(f"  venue:       {d.venue or 'unknown'}")
        if d.doi:
            print(f"  doi:         {d.doi}")
    else:
        print(f"  revision:    {d.revision}{auto}")
    print(f"  file:        {d.file}  ({'present' if on_disk else 'MISSING'})")
    if d.offset_known:
        offset = f"printed = pdf − {d.page_offset}"
    elif d.is_undoable("page_offset"):
        offset = "no page offset — undoable"
    else:
        offset = "page offset not yet checked"
    print(f"  pages:       {d.pages}  ({offset})")
    print(f"  text layer:  {d.text_layer}")
    print(f"  sha256:      {d.sha256}")
    if d.source_url:
        print(f"  source:      {d.source_url}")
    if d.added:
        print(f"  added:       {d.added}")
    if d.notes:
        print(f"  notes:       {d.notes}")
    for name, why in d.undoable.items():
        print(f"  undoable:    {name} — {why}")
    if d.toc:
        print(f"  toc:         {len(d.toc)} entries (`shelf toc {d.id}`)")
        for t in d.toc[: args.toc_lines]:
            print(f"    {t.section:<10} p.{t.page:<6} {t.title}")
        if len(d.toc) > args.toc_lines:
            print(f"    … {len(d.toc) - args.toc_lines} more (--toc-lines N)")
    else:
        print(f"  toc:         none (`shelf toc {d.id} --build`)")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    idx = _open_index(root)
    try:
        hits = idx.search(
            " ".join(args.query), manifest,
            part=args.part, doc_id=args.doc, limit=args.limit, raw=args.raw,
        )
    finally:
        idx.close()
    if not hits:
        print("no matches" + (" (is the index built? `shelf index`)" if not idx_has_docs(root) else ""))
        return 1
    for h in hits:
        doc = manifest.get(h.doc_id)
        rev = f" {doc.revision}" if doc.revision != "unknown" else ""
        loc = f"p. {h.printed_page}" if doc.offset_known else f"pdf p. {h.pdf_page}"
        sec = doc.enclosing_section(h.pdf_page)
        where = f"  §{sec.section} {sec.title}" if sec and sec.section else ""
        print(f"{h.doc_id}{rev}  {loc}{where}")
        print(f"    {h.snippet}")
    return 0


def idx_has_docs(root: Path) -> bool:
    path = db_path(root)
    if not path.exists():
        return False
    idx = Index(path)
    try:
        return bool(idx.indexed_ids())
    finally:
        idx.close()


def _parse_pages(spec: str) -> list[int]:
    """'12', '12-15', '12,14-16' → sorted unique page numbers."""
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ShelfError(f"bad page spec {part!r}; use N, N-M, or N,M-K")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a:
            a, b = b, a
        pages.update(range(a, b + 1))
    if not pages:
        raise ShelfError("empty page spec")
    return sorted(pages)


def _cite(doc: Document, pdf_page: int) -> str:
    label = doc.cite_label
    who = f" {label}" if label else ""
    if doc.offset_known:
        return f"{doc.id}{who}, p. {doc.printed_page(pdf_page)} (pdf p. {pdf_page})"
    return f"{doc.id}{who}, pdf p. {pdf_page} (printed page offset unknown)"


_SECTION_REF = re.compile(r"^§?\s*([A-Z]?\d+(?:\.\d+)+|[A-Z](?:\.\d+)+|§\d+)$")


def cmd_read(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    doc = manifest.get(args.id)

    # "§51.4.8" / "51.4.8" / "A.2": read a whole section via the TOC.
    if args.pages.startswith("§") or (_SECTION_REF.match(args.pages) and "-" not in args.pages and "," not in args.pages
                                      and "." in args.pages):
        if not doc.toc:
            raise ShelfError(f"{doc.id} has no table of contents — run `shelf toc {doc.id} --build`")
        entry = doc.section(args.pages)
        if entry is None:
            raise ShelfError(f"no section {args.pages.lstrip('§')} in {doc.id}'s TOC (try `shelf toc {doc.id} --grep …`)")
        first, last = doc.section_span(entry)
        if last - first + 1 > 25 and not args.all:
            last = first + 24
            print(f"note: §{entry.section} spans more than 25 pages; showing the first 25 (--all for everything)",
                  file=sys.stderr)
        pdf_pages = list(range(first, last + 1))
        print(f"§{entry.section} {entry.title}")
        return _print_pages(root, doc, pdf_pages)

    requested = _parse_pages(args.pages)
    if args.pdf or not doc.offset_known:
        pdf_pages = requested
        if not args.pdf:
            print(f"note: page offset for {doc.id} is unknown — treating numbers as PDF pages "
                  f"(run `shelf inspect` or `shelf edit --page-offset`)", file=sys.stderr)
    else:
        pdf_pages = [p + (doc.page_offset or 0) for p in requested]
    bad = [p for p in pdf_pages if p < 1 or p > doc.pages]
    if bad:
        raise ShelfError(f"pages out of range for {doc.id} (1–{doc.pages} pdf): {bad}")
    if len(pdf_pages) > 25 and not args.all:
        raise ShelfError(f"{len(pdf_pages)} pages requested; pass --all if you mean it")
    return _print_pages(root, doc, pdf_pages)


def _print_pages(root: Path, doc: Document, pdf_pages: list[int]) -> int:
    idx = _open_index(root)
    try:
        text_by_page = dict(idx.pages_for(doc.id))
    finally:
        idx.close()
    if not text_by_page:
        raise ShelfError(f"{doc.id} is not indexed — run `shelf index`")
    for p in pdf_pages:
        sec = doc.enclosing_section(p)
        where = f"  §{sec.section} {sec.title}" if sec and sec.section else (f"  {sec.title}" if sec else "")
        print(f"── {_cite(doc, p)}{where} ──")
        print(text_by_page.get(p, "").rstrip("\n"))
        print()
    return 0


def cmd_toc(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    doc = manifest.get(args.id)
    if args.build:
        pdf = root / doc.file
        if not pdf.is_file():
            raise ShelfError(f"{doc.file} is not on disk")
        items = toc_mod.extract_outline(pdf)
        if not items:
            print(f"{doc.id}: the PDF has no outline (bookmarks) — nothing to build")
            return 1
        if not doc.offset_known:
            print(f"note: page offset for {doc.id} is unknown; TOC printed pages assume offset 0 "
                  f"(run `shelf inspect` or `shelf edit --page-offset`, then rebuild)", file=sys.stderr)
        doc.toc = toc_mod.build_toc(doc, items, include_tables=args.tables, max_level=args.depth)
        manifest.save(manifest_path(root))
        print(f"{doc.id}: {len(doc.toc)} entries from {len(items)} outline items"
              + ("" if args.tables else " (tables/figures skipped; --tables to keep)"))
        return 0

    if not doc.toc:
        print(f"{doc.id}: no TOC yet — `shelf toc {doc.id} --build`")
        return 1
    pattern = re.compile(args.grep, re.I) if args.grep else None
    shown = 0
    for t in doc.toc:
        if args.depth is not None and t.level > args.depth:
            continue
        if pattern and not (pattern.search(t.title) or pattern.search(t.section)):
            continue
        indent = "  " * (t.level - 1)
        sec = f"§{t.section}" if t.section else ""
        loc = f"p. {t.page}" if doc.offset_known else f"pdf p. {t.pdf_page}"
        print(f"{indent}{sec:<12} {loc:<10} {t.title}")
        shown += 1
    if shown == 0:
        print("no matching entries")
        return 1
    return 0


def cmd_grep(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    if args.doc:
        manifest.get(args.doc)  # validate
    try:
        pattern = re.compile(args.pattern, re.I if args.ignore_case else 0)
    except re.error as e:
        raise ShelfError(f"bad regex: {e}") from None

    idx = _open_index(root)
    try:
        hits = list(idx.grep(pattern, doc_id=args.doc, part=args.part))
    finally:
        idx.close()
    if not hits:
        print("no matches")
        return 1
    shown = 0
    for did, pdf_page, text in hits:
        if shown >= args.limit:
            print(f"… {len(hits) - shown} more matching page(s); raise -n to see them")
            break
        doc = manifest.get(did)
        print(f"── {_cite(doc, pdf_page)} ──")
        lines = text.splitlines()
        match_idx = [i for i, ln in enumerate(lines) if pattern.search(ln)]
        shown_lines: set[int] = set()
        for i in match_idx:
            for j in range(max(0, i - args.context), min(len(lines), i + args.context + 1)):
                shown_lines.add(j)
        last = -2
        for j in sorted(shown_lines):
            if j != last + 1 and last >= 0:
                print("   …")
            marker = ">" if j in match_idx else " "
            print(f"{marker} {lines[j].rstrip()}")
            last = j
        print()
        shown += 1
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    targets = [manifest.get(i) for i in args.ids] if args.ids else list(manifest.documents)
    idx = _open_index(root)
    try:
        indexed = set(idx.indexed_ids())
        rows: list[list[str]] = []
        changed_docs = 0
        for doc in targets:
            if doc.id not in indexed:
                rows.append([doc.id, "—", "—", "not indexed"])
                continue
            result = inspection.inspect(doc, idx.pages_for(doc.id))
            off = "?" if result.offset is None else f"{result.offset.value} (pp. {result.offset.evidence[0]}…{result.offset.evidence[-1]}, {len(result.offset.evidence)} agree)"
            if doc.is_paper:
                b = result.biblio
                bits = []
                if b is not None:
                    if b.authors:
                        bits.append(format_surnames(b.authors))
                    if b.year:
                        bits.append(str(b.year))
                    if b.venue:
                        bits.append(b.venue)
                rev = ", ".join(bits) if bits else "?"
            else:
                rev = "?" if result.revision is None else f"{result.revision.value}" + (f" (pdf p. {result.revision.evidence[0]})" if result.revision.evidence else " (title)")
            status = ""
            if args.apply:
                changed = inspection.apply(doc, result, force=args.force)
                status = "set " + ", ".join(changed) if changed else "unchanged"
                changed_docs += bool(changed)
            else:
                pending = []
                if result.offset is not None and (args.force or doc.page_offset is None):
                    pending.append("page_offset")
                if result.revision is not None and (args.force or doc.revision == "unknown"):
                    pending.append("revision")
                if result.biblio is not None:
                    for name, cur in (("authors", doc.authors), ("year", doc.year), ("venue", doc.venue)):
                        if getattr(result.biblio, name) and (args.force or not cur):
                            pending.append(name)
                status = "would set " + ", ".join(pending) if pending else "nothing to do"
            rows.append([doc.id, off, rev, status])
    finally:
        idx.close()
    print(_table(rows, ["id", "page offset", "revision / byline", "action"]))
    if args.apply:
        manifest.save(manifest_path(root))
        print(f"\nupdated {changed_docs} document(s); guessed fields are marked `auto` — "
              f"confirm with `shelf edit` (clears the marker)")
    else:
        print("\ndry run — re-run with --apply to write the guesses")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    idx = _open_index(root)
    try:
        stats = idx.build(manifest, root, force=args.force)
    finally:
        idx.close()
    print(f"indexed {len(stats.indexed)}, unchanged {len(stats.skipped)}, missing files {len(stats.missing)}")
    for d in stats.indexed:
        print(f"  + {d}")
    for d in stats.missing:
        print(f"  ! {d} (file not on disk)")
    return 0


def cmd_wanted(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    if args.action == "add":
        manifest.add_wanted(Wanted(id=args.id, title=args.title or args.id, why=args.why or "", source=args.source or ""))
        manifest.save(manifest_path(root))
        print(f"wanted: {args.id}")
    elif args.action == "rm":
        manifest.remove_wanted(args.id)
        manifest.save(manifest_path(root))
        print(f"removed {args.id} from wanted")
    else:
        if not manifest.wanted:
            print("nothing wanted")
        for w in manifest.wanted:
            print(f"{w.id}\n  {w.title}")
            if w.why:
                print(f"  why:    {w.why}")
            if w.source:
                print(f"  source: {w.source}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    d = manifest.get(args.id)
    changed = []
    for attr in ("revision", "vendor", "title", "notes", "type", "venue", "doi"):
        val = getattr(args, attr)
        if val is not None:
            setattr(d, attr, val)
            changed.append(attr)
    if args.parts is not None:
        d.parts = args.parts
        changed.append("parts")
    if args.author is not None:
        d.authors = args.author
        changed.append("authors")
    if args.year is not None:
        d.year = args.year
        changed.append("year")
    if args.page_offset is not None:
        d.page_offset = args.page_offset
        changed.append("page_offset")
    if args.source is not None:
        d.source_url = args.source
        changed.append("source_url")
    if not changed:
        raise ShelfError("nothing to change")
    # A human-set value is no longer a guess, and a field someone just filled
    # was evidently not impossible to fill.
    d.auto = [a for a in d.auto if a not in changed]
    for name in changed:
        d.undoable.pop(name, None)
    # Re-run validation on the mutated dataclass.
    Document(**{k: v for k, v in d.__dict__.items()})
    manifest.save(manifest_path(root))
    print(f"{d.id}: updated {', '.join(changed)}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    paths = [Path(p).expanduser().resolve() for p in args.paths] if args.paths else None
    for p in paths or []:
        if not p.exists():
            raise ShelfError(f"no such path: {p}")
        if not p.is_relative_to(root):
            raise ShelfError(f"{p} is outside the shelf root {root}; `shelf add` copies external files in")

    plan = ingest_scan(root, manifest, paths)
    if plan.is_empty():
        print("nothing to ingest — every PDF under the root is catalogued")
        return 0

    if plan.relocated:
        print(f"relocated ({len(plan.relocated)}) — path fixed by content hash:")
        for doc, rel in plan.relocated:
            print(f"  {doc.id:<24} {doc.file}  →  {rel}")
    if plan.duplicates:
        print(f"duplicates ({len(plan.duplicates)}) — same content already catalogued, left in place:")
        for rel, dup_of in plan.duplicates:
            print(f"  {rel}  ==  {dup_of}")
    if plan.new:
        print(f"new ({len(plan.new)}) — guessed metadata, revision unknown:")
        rows = [[c.id, c.type, ", ".join(c.parts), str(c.pages), c.file] for c in plan.new]
        print(_table(rows, ["id", "type", "parts", "pages", "file"]))

    if not args.apply:
        print("\ndry run — re-run with --apply to catalog and index these")
        return 0

    idx = _open_index(root)
    try:
        ingest_apply(plan, manifest, root, idx)
    finally:
        idx.close()
    manifest.save(manifest_path(root))
    print(f"\ncatalogued {len(plan.new)} new, repaired {len(plan.relocated)} path(s); "
          f"run `shelf verify` and `shelf edit <id> --revision ...` to confirm metadata")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Integrity only: is the corpus intact? Metadata completeness is `debt`.

    Keeping them apart keeps the exit code meaningful. A missing file is
    something to fix now; an unknown revision is something to work through, and
    mixing them means `verify` never comes back clean and stops being read.
    """
    root, manifest = _load(args)
    problems = 0
    catalogued = set()
    for d in manifest.documents:
        path = root / d.file
        catalogued.add(path.resolve())
        if not path.is_file():
            print(f"MISSING  {d.id}: {d.file}")
            problems += 1
            continue
        if d.sha256 and sha256_file(path) != d.sha256:
            print(f"CHANGED  {d.id}: {d.file} does not match recorded sha256")
            problems += 1
    for pdf in sorted(root.rglob("*.pdf")):
        if pdf.resolve() not in catalogued and ".shelf" not in pdf.parts:
            print(f"ORPHAN   {pdf.relative_to(root)} is not in the manifest")
            problems += 1
    for toc in manifest.orphan_tocs(manifest_path(root)):
        print(f"ORPHAN   {toc.relative_to(root)} belongs to no catalogued document")
        problems += 1
    open_fields = debt_mod.total_open(manifest)
    print(f"{len(manifest.documents)} documents, {problems} problem(s)")
    if open_fields:
        print(f"{open_fields} open metadata field(s) — see `shelf debt`")
    return 1 if problems else 0


def cmd_debt(args: argparse.Namespace) -> int:
    _, manifest = _load(args)
    full = debt_mod.census(manifest, tier=args.tier)
    shown = debt_mod.census(manifest, tier=args.tier, limit=args.limit)

    if args.json:
        payload = {t: [asdict(g) for g in rows] for t, rows in shown.items()}
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    for tier, rows in full.items():
        ids = {g.doc_id for g in rows}
        shown_ids = {g.doc_id for g in shown[tier]}
        print(f"\n{tier.upper()}  {len(rows)} field(s) across {len(ids)} document(s)")
        for g in shown[tier]:
            print(f"  {g.doc_id:<50} {g.field:<13} {g.detail}")
        if len(ids) > len(shown_ids):
            print(f"  … {len(ids) - len(shown_ids)} more document(s)")
    total = debt_mod.total_open(manifest)

    retired = sum(len(d.undoable) for d in manifest.documents)
    print(f"\n{len(manifest.documents)} documents, {total} open field(s)", end="")
    print(f", {retired} retired as undoable" if retired else "")
    return 0


def cmd_undoable(args: argparse.Namespace) -> int:
    root, manifest = _load(args)
    d = manifest.get(args.id)
    if args.clear:
        if args.field not in d.undoable:
            raise ShelfError(f"{d.id}: {args.field!r} is not recorded as undoable")
        del d.undoable[args.field]
        print(f"{d.id}: {args.field} is open again")
    else:
        if not args.reason:
            raise ShelfError("a reason is required — what was checked, and why the answer does not exist")
        if args.field not in debt_mod.DEBT_FIELDS:
            raise ShelfError(f"{args.field!r} is not a curatable field; one of {', '.join(debt_mod.DEBT_FIELDS)}")
        d.undoable[args.field] = args.reason
        d.auto = [a for a in d.auto if a != args.field]
        print(f"{d.id}: {args.field} retired as undoable — {args.reason}")
    manifest.save(manifest_path(root))
    return 0


# -- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shelf", description="Catalog and search your datasheets and manuals.")
    p.add_argument("--version", action="version", version=f"shelf {__version__}")
    p.add_argument("--root", help=f"shelf root (directory containing {MANIFEST_NAME}); default: search upward from cwd")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create a shelf in a directory")
    s.add_argument("dir", nargs="?", default=".")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add", help="catalog a PDF (copies it under the root unless it is already there)")
    s.add_argument("pdf")
    s.add_argument("--id", required=True, help="short handle used in citations, e.g. rm0433")
    s.add_argument("--type", required=True, choices=DOC_TYPES)
    s.add_argument("--parts", nargs="*", metavar="PART", help="part numbers this document covers")
    s.add_argument("--vendor")
    s.add_argument("--title", help="default: PDF metadata title")
    s.add_argument("--revision", help="document revision from the cover, e.g. 'Rev 8'")
    s.add_argument("--author", nargs="*", metavar="NAME", help="paper byline, in order")
    s.add_argument("--year", type=int, help="publication year (papers)")
    s.add_argument("--venue", help="where it was published, e.g. JAES (papers)")
    s.add_argument("--doi")
    s.add_argument("--notes")
    s.add_argument("--source", help="where it came from (URL)")
    s.add_argument("--page-offset", type=int, default=None,
                   help="printed page = pdf page − offset (0 if they match; omit if unchecked)")
    s.add_argument("--filename", help="name to store under the root (default: original name)")
    s.add_argument("--move", action="store_true", help="move instead of copy")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("list", help="list catalogued and wanted documents")
    s.add_argument("--no-wanted", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="show one document's record")
    s.add_argument("id")
    s.add_argument("--toc-lines", type=int, default=15)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("search", help="full-text search across page text")
    s.add_argument("query", nargs="+")
    s.add_argument("--part", help="restrict to documents covering this part number (substring)")
    s.add_argument("--doc", help="restrict to one document id")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("--raw", action="store_true", help="pass the query to FTS5 unmodified (phrases, NEAR, OR, prefix*)")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("toc", help="show a document's table of contents, or --build it from the PDF outline")
    s.add_argument("id")
    s.add_argument("--build", action="store_true", help="extract the PDF outline into the manifest")
    s.add_argument("--tables", action="store_true", help="when building, keep Table/Figure entries")
    s.add_argument("--depth", type=int, help="max nesting level to build or show")
    s.add_argument("--grep", help="show only entries whose title or number matches (regex, case-insensitive)")
    s.set_defaults(func=cmd_toc)

    s = sub.add_parser("read", help="print pages of a document with a citation header")
    s.add_argument("id")
    s.add_argument("pages", help="printed pages N, N-M, N,M-K (PDF indices if offset unknown or --pdf); or a section §51.4.8")
    s.add_argument("--pdf", action="store_true", help="treat numbers as PDF page indices")
    s.add_argument("--all", action="store_true", help="allow more than 25 pages")
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("grep", help="regex over page text — for symbols FTS tokenizes away (ADCSEL[1:0], 2.2.21, 0x81A)")
    s.add_argument("pattern")
    s.add_argument("--doc", help="restrict to one document id")
    s.add_argument("--part", help="restrict to documents covering this part number (substring)")
    s.add_argument("-i", "--ignore-case", action="store_true")
    s.add_argument("-C", "--context", type=int, default=1, help="lines of context around each match (default 1)")
    s.add_argument("-n", "--limit", type=int, default=50, help="max matching pages to show")
    s.set_defaults(func=cmd_grep)

    s = sub.add_parser("inspect", help="guess page offsets (from running page numbers) and revisions (from covers)")
    s.add_argument("ids", nargs="*", help="document ids (default: all)")
    s.add_argument("--apply", action="store_true", help="write guesses for unknown fields (marked `auto`)")
    s.add_argument("--force", action="store_true", help="overwrite known values too")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("index", help="(re)build the full-text index from the manifest and PDFs")
    s.add_argument("--force", action="store_true", help="re-extract even if unchanged")
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("wanted", help="manage the list of documents you don't have yet")
    s.add_argument("action", nargs="?", choices=("list", "add", "rm"), default="list")
    s.add_argument("id", nargs="?")
    s.add_argument("--title")
    s.add_argument("--why")
    s.add_argument("--source")
    s.set_defaults(func=cmd_wanted)

    s = sub.add_parser("edit", help="update a document's metadata")
    s.add_argument("id")
    s.add_argument("--revision")
    s.add_argument("--author", nargs="*", metavar="NAME", help="paper byline, in order")
    s.add_argument("--year", type=int)
    s.add_argument("--venue")
    s.add_argument("--doi")
    s.add_argument("--vendor")
    s.add_argument("--title")
    s.add_argument("--notes")
    s.add_argument("--type", choices=DOC_TYPES)
    s.add_argument("--parts", nargs="*", metavar="PART")
    s.add_argument("--page-offset", type=int)
    s.add_argument("--source")
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("ingest", help="find uncatalogued PDFs under the root, detect moves/duplicates, guess metadata")
    s.add_argument("paths", nargs="*", help="directories or files under the root to scan (default: whole root)")
    s.add_argument("--apply", action="store_true", help="catalog and index the new documents (default: dry run)")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("verify", help="check files exist, hashes match, and no PDFs are uncatalogued")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("debt", help="open metadata fields, grouped by what could close them")
    s.add_argument("--tier", choices=debt_mod.TIERS, help="only this tier")
    s.add_argument("--limit", type=int, help="at most N documents per tier")
    s.add_argument("--json", action="store_true", help="machine-readable, for planning a batch")
    s.set_defaults(func=cmd_debt)

    s = sub.add_parser("undoable", help="record that a field cannot be filled from any source")
    s.add_argument("id")
    s.add_argument("field", choices=debt_mod.DEBT_FIELDS)
    s.add_argument("reason", nargs="?", help="what was checked, and why the answer does not exist")
    s.add_argument("--clear", action="store_true", help="reopen the field")
    s.set_defaults(func=cmd_undoable)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "wanted" and args.action in ("add", "rm") and not args.id:
        parser.error(f"wanted {args.action} requires an id")
    try:
        return int(args.func(args))
    except ShelfError as e:
        print(f"shelf: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
