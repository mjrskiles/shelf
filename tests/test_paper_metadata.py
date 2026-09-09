"""Papers are published, not revised: bylines, years, and venues."""

from __future__ import annotations

from shelf.inspect import detect_biblio
from shelf.manifest import Document, format_surnames


def _paper(**kw) -> Document:
    base = dict(id="dither", file="dither.pdf", type="paper", sha256="abc")
    base.update(kw)
    return Document(**base)


# -- citation labels ---------------------------------------------------------


def test_surnames_by_count() -> None:
    assert format_surnames(["JOHN VANDERKOOY"]) == "Vanderkooy"
    assert format_surnames(["JOHN VANDERKOOY", "STANLEY P. LIPSHITZ"]) == "Vanderkooy & Lipshitz"
    assert format_surnames(["A B", "C D", "E F"]) == "B et al."
    assert format_surnames([]) == ""


def test_paper_cites_by_byline_not_revision() -> None:
    d = _paper(authors=["JOHN VANDERKOOY", "STANLEY P. LIPSHITZ"], year=1987, venue="JAES")
    assert d.cite_label == "Vanderkooy & Lipshitz (1987), JAES"


def test_paper_cite_label_degrades_field_by_field() -> None:
    assert _paper(year=1987, venue="JAES").cite_label == "1987, JAES"
    assert _paper(authors=["RAY M. DOLBY"]).cite_label == "Dolby"
    assert _paper().cite_label == ""


def test_revised_document_still_cites_its_revision() -> None:
    d = Document(id="rm0433", file="r.pdf", type="reference-manual", revision="Rev 8")
    assert d.cite_label == "Rev 8"
    assert Document(id="x", file="x.pdf", type="datasheet").cite_label == ""


# -- cover detection ---------------------------------------------------------

JAES_COVER = """                    PAPERS

                 Dither in Digital Audio*

        JOHN VANDERKOOY        AND    STANLEY P. LIPSHITZ

University of Waterloo, Waterloo, Ont. N2L 3Gl, Canada

966                       J. AudioEng.Soc.,Vol.35,No.12,1987December
"""


def test_jaes_footer_gives_year_and_venue() -> None:
    b = detect_biblio([(1, JAES_COVER)])
    assert b is not None
    assert (b.year, b.venue) == (1987, "JAES")
    assert b.authors == ["JOHN VANDERKOOY", "STANLEY P. LIPSHITZ"]


def test_convention_line_gives_ordinal_venue_and_year() -> None:
    text = ("* Presented at the 76th Convention of the Audio Engineering\n"
            "Society, New York, 1984 October 8-11; revised 1987 September\n")
    b = detect_biblio([(1, text)])
    assert b is not None
    assert (b.year, b.venue) == (1984, "AES 76th Convention")


def test_jaes_outranks_a_convention_line_on_the_same_page() -> None:
    b = detect_biblio([(1, "Presented at the 76th Convention, 1984 October\n" + JAES_COVER)])
    assert b is not None
    assert (b.year, b.venue) == (1987, "JAES")


# An all-caps title has exactly the shape of an all-caps byline; getting this
# wrong writes a fabricated author into the catalog, so it is the case that
# matters most.
def test_all_caps_titles_are_not_bylines() -> None:
    for title in ("SPECIFIC ACOUSTIC WAVE ADMITTANCE",
                  "VIRTUAL STRINGED INSTRUMENTS",
                  "AUTOMATIC MICROPHONE MIXING",
                  "GEOMETRY OF SOUND PERCEPTION",
                  "A SIGNAL BIASING OUTPUT TRANSFORMERLESS TRANSISTOR AMPLIFIER"):
        b = detect_biblio([(1, f"PAPERS\n\n{title}\n\nsome abstract text\n")])
        assert b is None or not b.authors, f"{title!r} was read as a byline"


def test_address_lines_are_not_bylines() -> None:
    b = detect_biblio([(1, "PAPERS\n\nSome Title\n\nWaterloo, Ont., U. S. A.\n")])
    assert b is None or not b.authors


def test_returns_none_when_the_cover_says_nothing() -> None:
    assert detect_biblio([(1, "an ordinary paragraph of running text\n")]) is None


def test_initialless_byline_is_skipped_rather_than_guessed() -> None:
    # "KARLHEINZ BRANDENBURG" is indistinguishable in form from a two-word
    # title, so it is left for a human instead of being invented.
    b = detect_biblio([(1, "PAPERS\n\nMP3 and AAC Explained\n\nKARLHEINZ BRANDENBURG\n")])
    assert b is None or not b.authors
