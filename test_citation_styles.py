"""
Tests for citation_styles.py.
"""

from citation_styles import (
    detect_citation_style,
    format_reference,
)
from doi_lookup import Author


# ---------- detect_citation_style ----------

def test_detect_apa():
    text = "Fasuan, T. M. (2023). Okra mucilage films for postharvest fruit coating. Journal of Postharvest Science, 8(2), 45-58."
    assert detect_citation_style(text) == "apa"

def test_detect_mla():
    text = 'Fasuan, Titilope. "Okra Mucilage Films for Postharvest Fruit Coating." Journal of Postharvest Science, vol. 8, no. 2, 2023, pp. 45-58.'
    assert detect_citation_style(text) == "mla"

def test_detect_harvard():
    text = "Fasuan, T. (2023) 'Okra mucilage films for postharvest fruit coating', Journal of Postharvest Science, 8(2), pp. 45-58."
    assert detect_citation_style(text) == "harvard"

def test_detect_vancouver():
    text = "1. Fasuan TM, Bello K. Okra mucilage films for postharvest fruit coating. J Postharvest Sci. 2023;8(2):45-58."
    assert detect_citation_style(text) == "vancouver"

def test_detect_chicago_author_date():
    text = 'Fasuan, Titilope. 2023. "Okra Mucilage Films for Postharvest Fruit Coating." Journal of Postharvest Science 8, no. 2: 45-58.'
    assert detect_citation_style(text) == "chicago"

def test_detect_falls_back_to_apa_for_ambiguous_text():
    assert detect_citation_style("Some completely unformatted note about a paper") == "apa"

def test_detect_empty_text_defaults_to_apa():
    assert detect_citation_style("") == "apa"
    assert detect_citation_style(None) == "apa"


# ---------- format_reference: shared fixture data ----------

AUTHORS = [Author(given="Titilope M.", family="Fasuan"), Author(given="Kunle", family="Bello")]
TITLE = "Okra mucilage films for postharvest fruit coating"
YEAR = 2023
JOURNAL = "Journal of Postharvest Science"
VOLUME = "8"
ISSUE = "2"
PAGES = "45-58"
DOI = "10.1000/real123"


def test_format_apa_matches_expected_shape():
    result = format_reference("apa", TITLE, AUTHORS, YEAR, JOURNAL, VOLUME, ISSUE, PAGES, DOI)
    assert "Fasuan, T.M., & Bello, K." in result
    assert "(2023)." in result
    assert "8(2), 45-58" in result
    assert "https://doi.org/10.1000/real123" in result

def test_format_mla_matches_expected_shape():
    result = format_reference("mla", TITLE, AUTHORS, YEAR, JOURNAL, VOLUME, ISSUE, PAGES, DOI)
    assert "Fasuan, Titilope M., and Bello, Kunle." in result
    assert f'"{TITLE}."' in result
    assert "vol. 8," in result
    assert "no. 2," in result
    assert "pp. 45-58." in result

def test_format_chicago_matches_expected_shape():
    result = format_reference("chicago", TITLE, AUTHORS, YEAR, JOURNAL, VOLUME, ISSUE, PAGES, DOI)
    assert "2023." in result
    assert f'"{TITLE}."' in result
    assert "no. 2" in result
    assert "45-58." in result

def test_format_harvard_matches_expected_shape():
    result = format_reference("harvard", TITLE, AUTHORS, YEAR, JOURNAL, VOLUME, ISSUE, PAGES, DOI)
    assert f"'{TITLE}'," in result
    assert "(2023)" in result
    assert "8(2)," in result
    assert "pp. 45-58." in result

def test_format_vancouver_matches_expected_shape():
    result = format_reference("vancouver", TITLE, AUTHORS, YEAR, JOURNAL, VOLUME, ISSUE, PAGES, DOI)
    assert "Fasuan TM, Bello K." in result
    assert "2023;8(2):45-58." in result
    assert "doi:10.1000/real123" in result

def test_format_reference_unknown_style_falls_back_to_apa():
    result = format_reference("made_up_style", TITLE, AUTHORS, YEAR, JOURNAL, VOLUME, ISSUE, PAGES, DOI)
    assert result == format_reference("apa", TITLE, AUTHORS, YEAR, JOURNAL, VOLUME, ISSUE, PAGES, DOI)

def test_format_handles_missing_optional_fields_without_crashing():
    for style in ("apa", "mla", "chicago", "harvard", "vancouver"):
        result = format_reference(style, "", [], None, "", "", "", "", "")
        assert isinstance(result, str)
