"""
Tests for doi_lookup.py.

These mock requests.get so they run without live network access, and so
they're reproducible -- no test should ever depend on Crossref actually
being reachable. Run the real live-DOI check yourself once this is on
your machine (see the note at the bottom of this file).
"""

from unittest.mock import patch, MagicMock

import pytest

from doi_lookup import (
    clean_doi,
    is_valid_doi_format,
    parse_crossref_work,
    get_doi_metadata,
)


# ---------- clean_doi ----------

def test_clean_doi_strips_https_link():
    assert clean_doi("https://doi.org/10.1038/s41586-020-2649-2") == "10.1038/s41586-020-2649-2"

def test_clean_doi_strips_dx_doi_link():
    assert clean_doi("http://dx.doi.org/10.1000/abc123") == "10.1000/abc123"

def test_clean_doi_strips_doi_colon_prefix():
    assert clean_doi("doi: 10.1000/abc123") == "10.1000/abc123"

def test_clean_doi_handles_bare_doi():
    assert clean_doi("10.1000/abc123") == "10.1000/abc123"

def test_clean_doi_strips_whitespace_and_trailing_period():
    assert clean_doi("  10.1000/abc123.  ") == "10.1000/abc123"

def test_clean_doi_empty_input():
    assert clean_doi("") == ""
    assert clean_doi(None) == ""


# ---------- is_valid_doi_format ----------

def test_valid_doi_format_accepts_real_shapes():
    assert is_valid_doi_format("10.1038/s41586-020-2649-2")
    assert is_valid_doi_format("10.1000/182")

def test_valid_doi_format_rejects_garbage():
    assert not is_valid_doi_format("not a doi")
    assert not is_valid_doi_format("10.abc/xyz")
    assert not is_valid_doi_format("")


# ---------- parse_crossref_work ----------

def test_parse_crossref_work_full_record():
    work = {
        "title": ["Deep learning in genomics"],
        "author": [
            {"given": "Jane", "family": "Adebayo"},
            {"given": "K.", "family": "Bello"},
        ],
        "published-print": {"date-parts": [[2023, 5, 1]]},
        "container-title": ["Journal of African Bioinformatics"],
        "volume": "12",
        "issue": "3",
        "page": "120-137",
        "URL": "https://doi.org/10.1000/abc123",
    }
    result = parse_crossref_work(work, "10.1000/abc123")
    assert result.status == "found"
    assert result.title == "Deep learning in genomics"
    assert len(result.authors) == 2
    assert result.authors[0].full_name() == "Adebayo, Jane"
    assert result.year == 2023
    assert result.journal == "Journal of African Bioinformatics"
    assert result.volume == "12"
    assert result.issue == "3"
    assert result.pages == "120-137"

def test_parse_crossref_work_missing_optional_fields():
    # Real Crossref records are often missing volume/issue/pages -- must not crash.
    work = {
        "title": ["A minimal record"],
        "author": [],
    }
    result = parse_crossref_work(work, "10.1000/minimal")
    assert result.status == "found"
    assert result.title == "A minimal record"
    assert result.authors == []
    assert result.year is None
    assert result.volume == ""
    assert result.issue == ""

def test_parse_crossref_work_falls_back_through_date_fields():
    work = {
        "title": ["Online-first article"],
        "author": [],
        "published-online": {"date-parts": [[2021]]},
    }
    result = parse_crossref_work(work, "10.1000/online")
    assert result.year == 2021


# ---------- get_doi_metadata: input validation ----------

def test_get_doi_metadata_requires_mailto():
    with pytest.raises(ValueError):
        get_doi_metadata("10.1000/abc123", mailto="")

def test_get_doi_metadata_rejects_bad_mailto():
    with pytest.raises(ValueError):
        get_doi_metadata("10.1000/abc123", mailto="not-an-email")

def test_get_doi_metadata_invalid_doi_format():
    result = get_doi_metadata("this is not a doi", mailto="me@example.com")
    assert result.status == "invalid_doi"

def test_get_doi_metadata_empty_doi():
    result = get_doi_metadata("", mailto="me@example.com")
    assert result.status == "invalid_doi"


# ---------- get_doi_metadata: mocked network paths ----------

@patch("doi_lookup.requests.get")
def test_get_doi_metadata_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {
            "title": ["Test Paper"],
            "author": [{"given": "A.", "family": "Smith"}],
            "published-print": {"date-parts": [[2022]]},
            "container-title": ["Test Journal"],
            "volume": "5",
            "issue": "1",
            "page": "1-10",
        }
    }
    mock_get.return_value = mock_response

    result = get_doi_metadata("10.1000/test", mailto="me@example.com")
    assert result.status == "found"
    assert result.title == "Test Paper"
    assert result.year == 2022
    # confirm mailto was actually sent -- this is what puts us in the polite pool
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["mailto"] == "me@example.com"

@patch("doi_lookup.requests.get")
def test_get_doi_metadata_404_returns_not_found(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_get.return_value = mock_response

    result = get_doi_metadata("10.1000/doesnotexist", mailto="me@example.com")
    assert result.status == "not_found"

@patch("doi_lookup.requests.get")
def test_get_doi_metadata_empty_message_returns_not_found(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"message": {}}
    mock_get.return_value = mock_response

    result = get_doi_metadata("10.1000/empty", mailto="me@example.com")
    assert result.status == "not_found"

@patch("doi_lookup.requests.get")
@patch("doi_lookup.time.sleep", return_value=None)  # skip real waiting in tests
def test_get_doi_metadata_timeout_retries_then_errors(mock_sleep, mock_get):
    import requests as req
    mock_get.side_effect = req.exceptions.Timeout()

    result = get_doi_metadata("10.1000/timeout", mailto="me@example.com")
    assert result.status == "error"
    assert mock_get.call_count == 3  # MAX_RETRIES
    assert "timed out" in result.message.lower()

@patch("doi_lookup.requests.get")
@patch("doi_lookup.time.sleep", return_value=None)
def test_get_doi_metadata_rate_limit_then_succeeds(mock_sleep, mock_get):
    rate_limited = MagicMock()
    rate_limited.status_code = 429

    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {
        "message": {"title": ["Recovered After Retry"], "author": []}
    }

    mock_get.side_effect = [rate_limited, success]

    result = get_doi_metadata("10.1000/retry", mailto="me@example.com")
    assert result.status == "found"
    assert result.title == "Recovered After Retry"

@patch("doi_lookup.requests.get")
@patch("doi_lookup.time.sleep", return_value=None)
def test_get_doi_metadata_error_status_is_not_confused_with_not_found(mock_sleep, mock_get):
    server_error = MagicMock()
    server_error.status_code = 500
    mock_get.return_value = server_error

    result = get_doi_metadata("10.1000/servererror", mailto="me@example.com")
    # Critical: a server error must NEVER be reported as "not_found",
    # or a real reference could get wrongly flagged as fabricated.
    assert result.status == "error"
    assert result.status != "not_found"

@patch("doi_lookup.requests.get")
def test_get_doi_metadata_accepts_full_link_as_input(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"message": {"title": ["Link Input Works"], "author": []}}
    mock_get.return_value = mock_response

    result = get_doi_metadata("https://doi.org/10.1000/linktest", mailto="me@example.com")
    assert result.status == "found"
    assert result.doi == "10.1000/linktest"


# ----------------------------------------------------------------------
# LIVE TEST -- run this yourself once you have the file locally.
# This sandbox cannot reach api.crossref.org, so it has not been run here.
#
#   python -c "
#   from doi_lookup import get_doi_metadata
#   r = get_doi_metadata('10.1038/s41586-020-2649-2', mailto='you@example.com')
#   print(r)
#   "
# ----------------------------------------------------------------------
