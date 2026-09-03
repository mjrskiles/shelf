"""Command-line interface."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

from shelf import ShelfError, __version__
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
from shelf.manifest import DOC_TYPES, Document, Manifest, Wanted
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
    if doc.revision == "unknown":
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
    print(f"  revision:    {d.revision}")
    print(f"  file:        {d.file}  ({'present' if on_disk else 'MISSING'})")
    offset = f"printed = pdf − {d.page_offset}" if d.offset_known else "page offset not yet checked"
    print(f"  pages:       {d.pages}  ({offset})")
    print(f"  text layer:  {d.text_layer}")
    print(f"  sha256:      {d.sha256}")
    if d.source_url:
        print(f"  source:      {d.source_url}")
    if d.added:
        print(f"  added:       {d.added}")
    if d.notes:
        print(f"  notes:       {d.notes}")
    if d.toc:
        print(f"  toc:         {len(d.toc)} entries")
        for t in d.toc[: args.toc_lines]:
            print(f"    {t.section:<10} p.{t.page:<6} {t.title}")
        if len(d.toc) > args.toc_lines:
            print(f"    … {len(d.toc) - args.toc_lines} more (--toc-lines N)")
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
        print(f"{h.doc_id}{rev}  {loc}")
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
    for attr in ("revision", "vendor", "title", "notes", "type"):
        val = getattr(args, attr)
        if val is not None:
            setattr(d, attr, val)
            changed.append(attr)
    if args.parts is not None:
        d.parts = args.parts
        changed.append("parts")
    if args.page_offset is not None:
        d.page_offset = args.page_offset
        changed.append("page_offset")
    if args.source is not None:
        d.source_url = args.source
        changed.append("source_url")
    if not changed:
        raise ShelfError("nothing to change")
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
        if d.revision == "unknown":
            print(f"REVISION {d.id}: unknown — check the cover")
    for pdf in sorted(root.rglob("*.pdf")):
        if pdf.resolve() not in catalogued and ".shelf" not in pdf.parts:
            print(f"ORPHAN   {pdf.relative_to(root)} is not in the manifest")
            problems += 1
    print(f"{len(manifest.documents)} documents, {problems} problem(s)")
    return 1 if problems else 0


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
