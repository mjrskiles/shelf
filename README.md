# shelf

A catalog and full-text index for the datasheets, reference manuals, errata
sheets, and schematics you keep coming back to.

```
$ shelf search FIFOEN --part stm32h7
rm0433 Rev 8  p. 2041
    … the [FIFOEN] bit in the USART_CR1 register enables the FIFO mode …

$ shelf list
id            type              parts          rev     pages  on disk
------------  ----------------  -------------  ------  -----  -------
pcm3168a-ds   datasheet         PCM3168A       Rev. A  67     yes
rm0433        reference-manual  STM32H750xB    Rev 8   3353   yes

wanted (1):
  es0392       ES0392 — STM32H7 device errata
```

## Why

Reference manuals run to thousands of pages and don't fit in anyone's head or
context window. The failure mode is always the same: a plausible number stated
from memory with no source — a FIFO depth that was "8 or 16", a reset value
that was "presumably" right, an erratum nobody checked. `shelf` makes the
documents you already have searchable to the page, keeps track of the ones
you're missing, and gives you a stable way to cite what you found.

It is a plain command-line tool. Nothing about it requires an AI assistant,
though it was built so that one can use it.

## How it works

Three pieces, deliberately separate:

- **`shelf.json`** — the manifest. Human-editable, git-tracked, the source of
  truth. For each document: id, file, type, part numbers, revision, page
  count, sha256, printed-page offset, text-layer quality, table of contents,
  notes. Plus a `wanted` list of documents you don't have yet.
- **`.shelf/catalog.db`** — a derived SQLite database with an FTS5 full-text
  index over every page of every PDF. Rebuildable at any time from the
  manifest and the PDFs; never synced, never backed up, never edited by hand.
- **The PDFs** — sit next to the manifest. Usually gitignored (vendor
  copyright, size). A manifest can describe documents that aren't on this
  machine.

Text comes out of the PDFs via poppler (`pdftotext`, `pdfinfo`). No Python
dependencies.

## Install

```bash
pip install -e .            # or pipx install .
apt install poppler-utils   # Debian/Ubuntu; brew install poppler on macOS
```

## Use

```bash
shelf init docs/manuals                       # creates shelf.json + .shelf/
cd docs/manuals
shelf add ~/Downloads/rm0433.pdf --id rm0433 --type reference-manual \
      --parts STM32H750xB STM32H743xI --revision "Rev 8" \
      --source https://www.st.com/...          # copies the PDF in, hashes, indexes
shelf search fifo depth --part stm32h7         # tokens are ANDed as phrases
shelf search --raw 'FIFOEN OR RXFTIE'          # raw FTS5 syntax when you want it
shelf show rm0433
shelf edit rm0433 --page-offset 0 --revision "Rev 8"
shelf wanted add es0392 --title "ES0392 errata" --why "check §2.2.10"
shelf index                                    # rebuild after editing PDFs or the manifest
shelf verify                                   # missing files, changed hashes, uncatalogued PDFs
shelf ingest                                   # dry run: uncatalogued PDFs, moved files, duplicates
shelf ingest --apply                           # catalog them with guessed metadata (revision unknown)
```

### Ingesting a pile of PDFs

`shelf ingest` walks the root (or the paths you give it), hashes every PDF,
and sorts them into three buckets: **relocated** (content matches a
catalogued document whose file went missing — the path is repaired),
**duplicate** (same content as something already catalogued — reported,
left alone), and **new**. For new files it guesses an id (vendor document
numbers like `rm0433` or `es0392` win; otherwise a slug of the filename),
a type (from filename, PDF title, and directory names like `papers/`), and
part numbers (conservatively). Everything it adds is marked `revision:
unknown` with an "ingested — verify" note, so `shelf verify` keeps nagging
until you've confirmed the metadata with `shelf edit`.

A new file whose guessed id matches a `wanted` entry fulfils it.

`shelf` finds its root by walking up from the working directory to the
nearest `shelf.json`; `--root` or `SHELF_ROOT` override that.

### Printed pages vs PDF pages

Manuals number their pages from the first body page, so PDF page 20 might be
printed page 8. Record the difference once with `--page-offset` and every
search result reports the printed page — the one other documents and humans
cite. Until you set it, results say `pdf p. N` so nobody mistakes one for the
other.

### Citation convention

`<id> <revision> §<section>, p. <printed page>` — e.g.
`RM0433 Rev 8 §51.5.8, p. 2048`. Document revision is part of the fact;
`shelf verify` nags about documents whose revision is still `unknown`.

## Roadmap

- **remote sync** — `shelf sync` via rclone (SFTP today, S3-compatible
  storage later) so the PDFs live in one private place and the manifest in
  git.
- **ingest from outside the root** — today `ingest` scans under the root;
  pointing it at `~/Downloads` and having it copy files in is the next step.
- **toc** — populate `toc` from a document's own contents pages so lookups
  jump instead of searching.
- **export** — a static HTML catalog page.

## License

MIT.
