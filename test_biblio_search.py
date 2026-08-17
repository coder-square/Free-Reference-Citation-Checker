"""
Tests for biblio_search.py. All network calls are mocked -- api.crossref.org
and api.openalex.org are outside this sandbox's network allowlist, so these
tests validate the matching/scoring/branching logic, not live API behavior.
Run a real query yourself once this is on your machine (see bottom of file).
"""

from unittest.mock import patch, MagicMock

import pytest

from biblio_search import (
    search_reference,
    parse_openalex_work,
    _normalize,
    _score,
    HIGH_CONFIDENCE_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD,
)
from doi_lookup import Author


# ---------- helpers ----------

def crossref_response(items):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"message": {"items": items}}
    return resp

def openalex_response(results):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": results}
    return resp

GOOD_CROSSREF_ITEM = {
    "DOI": "10.1000/real123",
    "title": ["Okra Mucilage Films for Postharvest Fruit Coating"],
    "author": [{"given": "T. M.", "family": "Fasuan"}],
    "published-print": {"date-parts": [[2023]]},
    "container-title": ["Journal of Postharvest Science"],
    "volume": "8",
    "issue": "2",
    "page": "45-58",
}

WEAK_CROSSREF_ITEM = {
    "DOI": "10.1000/unrelated999",
    "title": ["Completely Unrelated Topic About Deep Sea Fish"],
    "author": [{"given": "X.", "family": "Zed"}],
    "published-print": {"date-parts": [[1998]]},
    "container-title": ["Marine Biology Quarterly"],
    "volume": "1",
    "issue": "1",
    "page": "1-2",
}

GOOD_OPENALEX_WORK = {
    "id": "https://openalex.org/W123",
    "title": "Okra Mucilage Films for Postharvest Fruit Coating",
    "publication_year": 2023,
    "doi": "https://doi.org/10.1000/real123",
    "authorships": [{"author": {"display_name": "T. M. Fasuan"}}],
    "primary_location": {"source": {"display_name": "Journal of Postharvest Science"}},
    "biblio": {"volume": "8", "issue": "2", "first_page": "45", "last_page": "58"},
}


# ---------- input validation ----------

def test_requires_mailto():
    with pytest.raises(ValueError):
        search_reference("Fasuan, T.M. (2023). Okra mucilage films.", mailto="")

def test_rejects_bad_mailto():
    with pytest.raises(ValueError):
        search_reference("Fasuan, T.M. (2023). Okra mucilage films.", mailto="not-an-email")

def test_empty_query_returns_invalid_input():
    result = search_reference("   ", mailto="me@example.com")
    assert result.status == "invalid_input"


# ---------- scoring sanity checks ----------

def test_normalize_strips_punctuation_and_case():
    # Punctuation becomes whitespace (not deleted), so "T.M." -> "t m", two tokens.
    # That's fine for rapidfuzz's token-based scoring, which doesn't care about
    # token count -- this test just locks in the actual, correct behavior.
    assert _normalize("Fasuan, T.M. (2023)!!") == "fasuan t m 2023"
    assert _normalize("  Multiple   Spaces--Here  ") == "multiple spaces here"

def test_score_high_for_near_identical_text():
    authors = [Author(given="T. M.", family="Fasuan")]
    score = _score(
        "Fasuan TM 2023 Okra mucilage films for postharvest fruit coating",
        "Okra Mucilage Films for Postharvest Fruit Coating",
        authors,
        2023,
    )
    assert score >= HIGH_CONFIDENCE_THRESHOLD

def test_score_low_for_unrelated_text():
    authors = [Author(given="X.", family="Zed")]
    score = _score(
        "Fasuan TM 2023 Okra mucilage films for postharvest fruit coating",
        "Completely Unrelated Topic About Deep Sea Fish",
        authors,
        1998,
    )
    assert score < LOW_CONFIDENCE_THRESHOLD


# ---------- parse_openalex_work ----------

def test_parse_openalex_work_full_record():
    candidate = parse_openalex_work(GOOD_OPENALEX_WORK)
    assert candidate.source == "openalex"
    assert candidate.doi == "10.1000/real123"
    assert candidate.title == "Okra Mucilage Films for Postharvest Fruit Coating"
    assert candidate.year == 2023
    assert candidate.journal == "Journal of Postharvest Science"
    assert candidate.volume == "8"
    assert candidate.pages == "45-58"
    assert len(candidate.authors) == 1
    assert candidate.authors[0].family == "Fasuan"

def test_parse_openalex_work_missing_fields_does_not_crash():
    minimal = {"title": "Bare Record"}
    candidate = parse_openalex_work(minimal)
    assert candidate.title == "Bare Record"
    assert candidate.authors == []
    assert candidate.year is None
    assert candidate.volume == ""


# ---------- search_reference: crossref strong match, no openalex call ----------

@patch("biblio_search.requests.get")
def test_crossref_strong_match_skips_openalex(mock_get):
    mock_get.return_value = crossref_response([GOOD_CROSSREF_ITEM])

    result = search_reference(
        "Fasuan TM (2023). Okra mucilage films for postharvest fruit coating. "
        "Journal of Postharvest Science 8(2) 45-58.",
        mailto="me@example.com",
    )

    assert result.status == "matched"
    assert result.best_candidate.source == "crossref"
    assert mock_get.call_count == 1  # OpenAlex must NOT have been called


# ---------- search_reference: crossref weak, openalex strong ----------

@patch("biblio_search.requests.get")
def test_crossref_weak_openalex_strong_matches_via_openalex(mock_get):
    def side_effect(url, headers=None, params=None, timeout=None):
        if "crossref" in url:
            return crossref_response([WEAK_CROSSREF_ITEM])
        return openalex_response([GOOD_OPENALEX_WORK])

    mock_get.side_effect = side_effect

    result = search_reference(
        "Fasuan TM (2023). Okra mucilage films for postharvest fruit coating.",
        mailto="me@example.com",
    )

    assert result.status == "matched"
    assert result.best_candidate.source == "openalex"
    assert mock_get.call_count == 2  # both sources checked


# ---------- search_reference: both weak -> no_match, not silently accepted ----------

@patch("biblio_search.requests.get")
def test_both_sources_weak_returns_no_match(mock_get):
    def side_effect(url, headers=None, params=None, timeout=None):
        if "crossref" in url:
            return crossref_response([WEAK_CROSSREF_ITEM])
        return openalex_response([])

    mock_get.side_effect = side_effect

    result = search_reference(
        "Fasuan TM (2023). Okra mucilage films for postharvest fruit coating.",
        mailto="me@example.com",
    )

    assert result.status == "no_match"


# ---------- search_reference: both sources return nothing at all ----------

@patch("biblio_search.requests.get")
def test_both_sources_empty_returns_no_match(mock_get):
    def side_effect(url, headers=None, params=None, timeout=None):
        if "crossref" in url:
            return crossref_response([])
        return openalex_response([])

    mock_get.side_effect = side_effect

    result = search_reference("Some obscure unpublished thesis title", mailto="me@example.com")
    assert result.status == "no_match"
    assert result.best_candidate is None


# ---------- search_reference: error handling must never look like no_match ----------

@patch("biblio_search.requests.get")
@patch("biblio_search.time.sleep", return_value=None)
def test_both_sources_error_returns_error_not_no_match(mock_sleep, mock_get):
    mock_get.side_effect = __import__("requests").exceptions.ConnectionError()

    result = search_reference("Fasuan TM (2023). Okra mucilage films.", mailto="me@example.com")
    assert result.status == "error"
    assert result.status != "no_match"

@patch("biblio_search.requests.get")
@patch("biblio_search.time.sleep", return_value=None)
def test_one_source_errors_other_empty_is_still_error_not_no_match(mock_sleep, mock_get):
    """
    Critical case: Crossref fails outright, OpenAlex successfully returns
    zero results. This must NOT be reported as a clean 'no_match', because
    only one of two sources was actually checked -- reporting it as a
    confirmed absence risks a false hallucination flag.
    """
    import requests as req

    def side_effect(url, headers=None, params=None, timeout=None):
        if "crossref" in url:
            raise req.exceptions.ConnectionError()
        return openalex_response([])

    mock_get.side_effect = side_effect

    result = search_reference("Fasuan TM (2023). Okra mucilage films.", mailto="me@example.com")
    assert result.status == "error"
    assert result.status != "no_match"


# ----------------------------------------------------------------------
# LIVE TEST -- run yourself once this is on your machine; this sandbox
# cannot reach api.crossref.org or api.openalex.org.
#
#   python -c "
#   from biblio_search import search_reference
#   r = search_reference(
#       'Fasuan TM et al 2023 okra mucilage edible coating postharvest',
#       mailto='you@example.com')
#   print(r.status, r.best_candidate)
#   "
# ----------------------------------------------------------------------
