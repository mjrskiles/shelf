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
  notes. Papers carry a byline instead of a revision — authors, year, venue,
  doi. Plus a `wanted` list of documents you don't have yet.
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
shelf grep 'ADCSEL\[1:0\]' --doc rm0433        # regex, for what FTS tokenizes away
shelf read rm0433 2248-2249                    # page text with a citation header
shelf toc rm0433 --build                       # table of contents from the PDF outline
shelf toc rm0433 --grep 'clock generator'      # find a section by title
shelf read rm0433 §51.4.8                      # read a whole section
shelf inspect --apply                          # guess page offsets + revisions, marked `auto`
shelf show rm0433
shelf edit rm0433 --page-offset 0 --revision "Rev 8"
shelf wanted add es0392 --title "ES0392 errata" --why "check §2.2.10"
shelf index                                    # rebuild after editing PDFs or the manifest
shelf verify                                   # missing files, changed hashes, uncatalogued PDFs
shelf debt                                     # open metadata fields, by what could close them
shelf undoable <id> revision "date only"       # this field cannot be filled — stop asking
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
unknown` with an "ingested — verify" note, so `shelf debt` keeps listing them
until you've confirmed the metadata with `shelf edit`.

A new file whose guessed id matches a `wanted` entry fulfils it.

`shelf` finds its root by walking up from the working directory to the
nearest `shelf.json`; `--root` or `SHELF_ROOT` override that.

### search vs grep vs read

`search` is FTS5 — fast, ranked, good for words and phrases. `grep` is a
regex over the same stored page text, for the things FTS tokenizes away:
`ADCSEL[1:0]`, `2.2.21`, `0x81A`, `§51.4.8`. Both return page numbers;
`read` prints the pages themselves, each under a copy-ready citation line
(`rm0433 Rev 8, p. 2248 (pdf p. 2248)`). None of the three touch the PDF
again — they read the index, so they work at the speed of SQLite.

### toc — sections, not just pages

`shelf toc <id> --build` reads the PDF's bookmark outline (via `pdftohtml`,
no page rendering — a 3000-page manual takes under a second) into the
manifest as `{section, title, page, pdf_page, level}` entries, splitting
"51.4.8 SAI clock generator" into number and title and skipping Table/Figure
bookmarks unless `--tables`. Then `shelf toc <id>` lists it (`--depth`,
`--grep`), `shelf read <id> §51.4.8` prints the whole section, and `search`
and `read` label every page with its enclosing section. PDFs without
bookmarks get an honest "no outline" — most reference manuals have one, many
short datasheets don't.

### inspect — let the documents describe themselves

`shelf inspect` samples the middle of each document for running page
numbers (`2081/3353`, `page 13/73`) and solves for the printed-page offset,
and scans the title and cover for a revision (`Rev 8`, `Rev. B`, `v1.0.5`).
It only believes an offset when several pages agree. `--apply` writes the
guesses for fields that are still unknown and records them in the document's
`auto` list; `shelf debt` keeps them in its `confirm` tier until `shelf edit`
confirms them, which clears the marker. `--force` re-guesses known values too,
but never touches a field retired as undoable.

### verify vs debt — intact vs complete

Two different questions, deliberately kept apart.

`shelf verify` asks whether the corpus is **intact**: files present, hashes
matching, no stray PDFs. Its exit code means something because it can actually
come back clean.

`shelf debt` asks whether the metadata is **complete**, and groups what is open
by the cheapest thing that could close it:

| tier | what closes it |
|------|----------------|
| `confirm` | a value `inspect` guessed — corroborate it on a page the detector didn't vote on |
| `cover` | the document's own front matter: revision, title, paper byline |
| `offset` | printed folio minus PDF index, read off two widely separated pages |
| `web` | not in the document at all — `source_url`, and years no cover states |

`--tier` and `--limit` carve out a batch, and `--json` makes it a work list.
Batches are sorted by id, so the same `--limit 8` hands out the same eight
documents every time.

Mixing the two questions was the mistake worth fixing: an unknown revision
would sit in `verify`'s output forever, so `verify` never came back clean and
stopped being read.

### undoable — the answer that does not exist

Some fields cannot be filled from any source. A scanned paper with no folio on
any page has no printed-page offset; a manual that identifies itself by date and
document number has no revision. That is different from unknown — unknown means
nobody has looked yet — and recording the difference is what stops every future
pass re-reading the same dead ends.

```bash
shelf undoable ad2-rm revision "identifies itself by date and DOC# only"
shelf undoable ad2-rm revision --clear     # reopen it
```

A reason is required: "cannot be filled" is a finding, and a finding has
evidence behind it. Retired fields leave `shelf debt`, are reported separately
in its total, and are skipped by `inspect --apply` even under `--force` —
someone read the document, which outranks a pattern match. Filling the field by
hand with `shelf edit` retires the retirement automatically.

### Printed pages vs PDF pages

Manuals number their pages from the first body page, so PDF page 20 might be
printed page 8. Record the difference once with `--page-offset` and every
search result reports the printed page — the one other documents and humans
cite. Until you set it, results say `pdf p. N` so nobody mistakes one for the
other.

### Citation convention

`<id> <revision> §<section>, p. <printed page>` — e.g.
`RM0433 Rev 8 §51.5.8, p. 2048`. Document revision is part of the fact;
`shelf debt` lists documents whose revision is still `unknown` or still
marked `auto`.

### Papers are published, not revised

A `paper` has no revision and never will, so shelf identifies it by byline:
`authors`, `year`, `venue`, `doi`. Citations become
`dither-in-digital-audio Vanderkooy & Lipshitz (1987), JAES, p. 966`, and
`shelf debt` asks for authors and year rather than nagging forever for a
revision that does not exist.

`shelf inspect` reads AES covers for this: the running JAES footer
(`J. Audio Eng. Soc., Vol. 35, No. 12, 1987 December`) and the
`Presented at the 76th Convention` line. It takes the JAES footer over the
convention line, because the journal publication is the version of record.

Bylines are the one thing it will not guess at. An all-caps title
(`SPECIFIC ACOUSTIC WAVE ADMITTANCE`) has the same shape as an all-caps
byline, so a name is only believed when it carries an initial —
`RICHARD C. HEYSER`, `R. A. GREINER`. That misses plain two-token bylines
(`KARLHEINZ BRANDENBURG`), which is the intended trade: a missing byline is a
prompt to go look, a fabricated one is a lie in your citations. Fill the rest
in by hand:

```bash
shelf edit mp3-and-aac-explained --author "Karlheinz Brandenburg" --year 1999 --venue "AES 17th Conference"
```

## Roadmap

- **remote sync** — `shelf sync` via rclone (SFTP today, S3-compatible
  storage later) so the PDFs live in one private place and the manifest in
  git.
- **ingest from outside the root** — today `ingest` scans under the root;
  pointing it at `~/Downloads` and having it copy files in is the next step.
- **toc from contents pages** — for PDFs with no bookmark outline, parse
  the printed contents pages instead.
- **export** — a static HTML catalog page.

## License

MIT.
