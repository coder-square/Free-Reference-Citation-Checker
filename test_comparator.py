"""
Tests for comparator.py. get_doi_metadata and search_reference are mocked
at the point comparator.py calls them, so these tests validate the decision
logic and flagging rules, not live API behavior.
"""

from unittest.mock import patch

import pytest

from comparator import (
    extract_doi_from_text,
    extract_year_from_text,
    extract_volume_issue_from_text,
    detect_initials_style,
    check_initials_consistency,
    format_authors_apa,
    format_apa_reference,
    compare_authors_to_text,
    check_reference,
    check_reference_list,
)
from doi_lookup import Author, DoiResult
from biblio_search import Candidate, BiblioSearchResult


# ---------- extraction helpers ----------

def test_extract_doi_bare():
    assert extract_doi_from_text("Fasuan T. 2023. Title. 10.1000/abc123") == "10.1000/abc123"

def test_extract_doi_from_link():
    assert extract_doi_from_text("See https://doi.org/10.1000/abc123 for details.") == "10.1000/abc123"

def test_extract_doi_strips_trailing_punctuation():
    assert extract_doi_from_text("Title. doi.org/10.1000/abc123.") == "10.1000/abc123"

def test_extract_doi_none_present():
    assert extract_doi_from_text("Fasuan T. 2023. A title with no doi at all.") is None


def test_extract_year_parenthetical():
    assert extract_year_from_text("Fasuan, T. (2023). Okra mucilage films.") == 2023

def test_extract_year_bare():
    assert extract_year_from_text("Fasuan T 2021 Okra mucilage films") == 2021

def test_extract_year_none():
    assert extract_year_from_text("Fasuan T. Okra mucilage films, no date given.") is None


def test_extract_volume_issue_paren_style():
    assert extract_volume_issue_from_text("Journal of Science, 12(3), 45-58.") == ("12", "3")

def test_extract_volume_issue_worded_style():
    assert extract_volume_issue_from_text("Vol. 12, No. 3, pp. 45-58.") == ("12", "3")

def test_extract_volume_issue_absent():
    assert extract_volume_issue_from_text("Journal of Science, pp. 45-58.") == (None, None)


# ---------- initials style detection ----------

def test_detect_style_surname_comma_initials():
    assert detect_initials_style("Fasuan, T. M. (2023). Title.") == "surname_comma_initials"

def test_detect_style_initials_first():
    assert detect_initials_style("T. M. Fasuan (2023). Title.") == "initials_first"

def test_detect_style_surname_no_periods():
    assert detect_initials_style("Fasuan TM (2023) Title.") == "surname_no_periods"

def test_detect_style_unknown_for_garbage():
    assert detect_initials_style("2023 some title with no clear author") == "unknown"

def test_check_initials_consistency_flags_minority_style():
    entries = [
        "Fasuan, T. M. (2023). Title one.",
        "Bello, K. (2022). Title two.",
        "A. Olagunju (2021). Title three.",  # different style
    ]
    result = check_initials_consistency(entries)
    assert result["dominant_style"] == "surname_comma_initials"
    assert result["flagged_indices"] == [2]

def test_check_initials_consistency_all_unknown():
    result = check_initials_consistency(["no clear pattern here", "another vague one"])
    assert result["dominant_style"] == "unknown"
    assert result["flagged_indices"] == []


# ---------- author formatting ----------

def test_format_authors_apa_single():
    assert format_authors_apa([Author(given="Titilope M.", family="Fasuan")]) == "Fasuan, T.M."

def test_format_authors_apa_two():
    authors = [Author(given="T.", family="Fasuan"), Author(given="K.", family="Bello")]
    assert format_authors_apa(authors) == "Fasuan, T., & Bello, K."

def test_format_authors_apa_three_plus():
    authors = [
        Author(given="T.", family="Fasuan"),
        Author(given="K.", family="Bello"),
        Author(given="A.", family="Olagunju"),
    ]
    result = format_authors_apa(authors)
    assert result.startswith("Fasuan, T., Bello, K.")
    assert result.endswith("& Olagunju, A.")

def test_format_authors_apa_empty():
    assert format_authors_apa([]) == ""

def test_format_apa_reference_full():
    authors = [Author(given="T. M.", family="Fasuan")]
    result = format_apa_reference(
        title="Okra mucilage films",
        authors=authors,
        year=2023,
        journal="Journal of Postharvest Science",
        volume="8",
        issue="2",
        pages="45-58",
        doi="10.1000/abc123",
    )
    assert "Fasuan, T.M." in result
    assert "(2023)" in result
    assert "Okra mucilage films." in result
    assert "8(2), 45-58" in result
    assert "https://doi.org/10.1000/abc123" in result


# ---------- author-presence check ----------

def test_compare_authors_flags_missing_surname():
    verified = [Author(given="T.", family="Fasuan"), Author(given="K.", family="Bello")]
    issues = compare_authors_to_text(verified, "Fasuan, T. (2023). Some title.")
    assert len(issues) == 1
    assert "Bello" in issues[0]

def test_compare_authors_no_issues_when_all_present():
    verified = [Author(given="T.", family="Fasuan")]
    issues = compare_authors_to_text(verified, "Fasuan, T. (2023). Some title.")
    assert issues == []


# ---------- check_reference: full decision flow ----------

def test_check_reference_empty_input():
    result = check_reference("", mailto="me@example.com")
    assert result.status == "invalid"

FOUND_DOI_RESULT = DoiResult(
    status="found",
    doi="10.1000/real123",
    title="Okra mucilage films for postharvest fruit coating",
    authors=[Author(given="T. M.", family="Fasuan")],
    year=2023,
    journal="Journal of Postharvest Science",
    volume="8",
    issue="2",
    pages="45-58",
)

@patch("comparator.get_doi_metadata")
def test_check_reference_doi_verifies_cleanly(mock_doi):
    mock_doi.return_value = FOUND_DOI_RESULT
    text = (
        "Fasuan, T. M. (2023). Okra mucilage films for postharvest fruit coating. "
        "Journal of Postharvest Science, 8(2), 45-58. https://doi.org/10.1000/real123"
    )
    result = check_reference(text, mailto="me@example.com")
    assert result.status == "verified"
    assert result.source == "crossref_doi"
    assert result.corrected_entry != ""
    assert result.verified_doi == "10.1000/real123"

@patch("comparator.get_doi_metadata")
def test_check_reference_doi_verifies_but_year_and_volume_missing(mock_doi):
    mock_doi.return_value = FOUND_DOI_RESULT
    # No year, no volume/issue in the text -- should be flagged and auto-filled.
    text = "Fasuan, T. M. Okra mucilage films for postharvest fruit coating. Journal of Postharvest Science. https://doi.org/10.1000/real123"
    result = check_reference(text, mailto="me@example.com")
    assert result.status == "verified_with_corrections"
    assert any("Volume number missing" in i for i in result.issues)
    assert any("Issue number missing" in i for i in result.issues)
    assert any("No year found" in i for i in result.issues)
    assert "8(2)" in result.corrected_entry  # correction actually applied

@patch("comparator.search_reference")
@patch("comparator.get_doi_metadata")
def test_check_reference_doi_resolves_to_wrong_paper_falls_back(mock_doi, mock_search):
    # DOI resolves successfully, but to something totally unrelated to the reference text.
    unrelated = DoiResult(
        status="found", doi="10.1000/real123",
        title="Completely unrelated deep sea fish taxonomy study",
        authors=[Author(given="X.", family="Zed")], year=1998,
    )
    mock_doi.return_value = unrelated
    mock_search.return_value = BiblioSearchResult(status="no_match", query="...")

    text = "Fasuan, T. M. (2023). Okra mucilage films for postharvest fruit coating. https://doi.org/10.1000/real123"
    result = check_reference(text, mailto="me@example.com")

    assert result.status == "flagged_unverifiable"
    assert any("resolves to a different work" in i for i in result.issues)
    mock_search.assert_called_once()  # fallback search must have been attempted

@patch("comparator.search_reference")
@patch("comparator.get_doi_metadata")
def test_check_reference_doi_not_found_falls_back_and_matches(mock_doi, mock_search):
    mock_doi.return_value = DoiResult(status="not_found", doi="10.1000/typo123")
    candidate = Candidate(
        source="crossref", doi="10.1000/real123",
        title="Okra mucilage films for postharvest fruit coating",
        authors=[Author(given="T.", family="Fasuan")], year=2023,
        journal="Journal of Postharvest Science", volume="8", issue="2", pages="45-58",
        score=95.0,
    )
    mock_search.return_value = BiblioSearchResult(status="matched", best_candidate=candidate, all_candidates=[candidate])

    text = "Fasuan, T. (2023). Okra mucilage films for postharvest fruit coating. doi.org/10.1000/typo123"
    result = check_reference(text, mailto="me@example.com")

    # Status is verified_with_corrections, not verified -- the text has no
    # volume/issue, so those get flagged and filled in from the record.
    # That's correct behavior, not a bug: this test originally asserted the
    # wrong expectation and has been corrected to match.
    assert result.status == "verified_with_corrections"
    assert any("does not exist in Crossref" in i for i in result.issues)
    assert any("Volume number missing" in i for i in result.issues)
    assert result.verified_doi == "10.1000/real123"  # corrected DOI, not the typo

@patch("comparator.search_reference")
def test_check_reference_no_doi_matches_via_biblio_search(mock_search):
    candidate = Candidate(
        source="openalex", doi="10.1000/real123",
        title="Okra mucilage films for postharvest fruit coating",
        authors=[Author(given="T.", family="Fasuan")], year=2023,
        score=90.0,
    )
    mock_search.return_value = BiblioSearchResult(status="matched", best_candidate=candidate)

    text = "Fasuan T (2023) Okra mucilage films for postharvest fruit coating"
    result = check_reference(text, mailto="me@example.com")
    assert result.status == "verified"
    assert result.source == "openalex_search"

@patch("comparator.search_reference")
def test_check_reference_no_doi_no_match_is_flagged_not_confirmed_fake(mock_search):
    mock_search.return_value = BiblioSearchResult(status="no_match", message="No matching record found in Crossref or OpenAlex.")
    text = "Some completely fabricated citation with no real source"
    result = check_reference(text, mailto="me@example.com")
    assert result.status == "flagged_unverifiable"
    assert result.status != "unable_to_verify"  # this was a clean check, not a technical failure

@patch("comparator.search_reference")
def test_check_reference_search_error_is_unable_to_verify_not_flagged(mock_search):
    """
    Critical distinction: a technical failure during search must produce
    'unable_to_verify', never 'flagged_unverifiable' -- conflating the two
    risks falsely branding a real reference as fabricated just because of
    a network hiccup.
    """
    mock_search.return_value = BiblioSearchResult(status="error", message="Both lookups failed.")
    text = "Fasuan T (2023) Okra mucilage films"
    result = check_reference(text, mailto="me@example.com")
    assert result.status == "unable_to_verify"
    assert result.status != "flagged_unverifiable"

@patch("comparator.search_reference")
def test_check_reference_possible_match_flagged_for_review(mock_search):
    candidate = Candidate(source="crossref", title="Okra films", authors=[], year=2023, score=70.0)
    mock_search.return_value = BiblioSearchResult(status="possible_match", best_candidate=candidate)
    result = check_reference("Fasuan T Okra films 2023", mailto="me@example.com")
    assert result.status == "possible_mismatch"


# ---------- check_reference_list ----------

@patch("comparator.search_reference")
def test_check_reference_list_runs_all_and_flags_style(mock_search):
    mock_search.return_value = BiblioSearchResult(status="no_match", message="not found")
    entries = [
        "Fasuan, T. M. (2023). Title one.",
        "Bello, K. (2022). Title two.",
        "A. Olagunju (2021). Title three.",  # different style
    ]
    output = check_reference_list(entries, mailto="me@example.com")
    assert len(output["results"]) == 3
    assert output["initials_consistency"]["flagged_indices"] == [2]
    assert mock_search.call_count == 3
