"""
biblio_search.py

For references that don't have a DOI (or where the DOI lookup failed),
search Crossref's bibliographic endpoint for the closest matching record,
scored by similarity. If Crossref doesn't produce a confident match, fall
back to OpenAlex, which indexes some records Crossref misses.

Every outcome gets an explicit status -- nothing is silently accepted
or silently rejected:
  "matched"         - a candidate scored above the high-confidence threshold
  "possible_match"  - a candidate was found but similarity is only moderate;
                       needs a human to look at it before accepting
  "no_match"        - both sources were checked cleanly and neither
                       produced anything above the low-confidence floor
  "invalid_input"   - empty/blank reference text
  "error"           - one or both sources could not be reached; this is
                       NOT the same as "no_match" and must never be
                       reported to a user as "reference not found"

Design note: a reference is only ever eligible to be called "no_match"
(and, upstream, considered for a hallucination flag) after BOTH Crossref
and OpenAlex have been successfully queried and both came back empty or
low-scoring. If either source couldn't be reached, the result is "error",
because a partial check is not the same as a completed negative check.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from rapidfuzz import fuzz

from doi_lookup import (
    Author,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    clean_doi,
    parse_crossref_work,
)

CROSSREF_SEARCH_URL = "https://api.crossref.org/works"
OPENALEX_SEARCH_URL = "https://api.openalex.org/works"

# Similarity scores are 0-100 (rapidfuzz token_set_ratio).
HIGH_CONFIDENCE_THRESHOLD = 85  # accept as a genuine match
LOW_CONFIDENCE_THRESHOLD = 60   # below this, treat as no real match at all


@dataclass
class Candidate:
    source: str  # "crossref" | "openalex"
    doi: str = ""
    title: str = ""
    authors: list = field(default_factory=list)
    year: Optional[int] = None
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    score: float = 0.0
    url: str = ""


@dataclass
class BiblioSearchResult:
    status: str  # "matched" | "possible_match" | "no_match" | "invalid_input" | "error"
    message: str = ""
    query: str = ""
    best_candidate: Optional[Candidate] = None
    all_candidates: list = field(default_factory=list)


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation so scoring isn't thrown off by formatting noise."""
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_signature(title: str, authors: list, year) -> str:
    author_names = " ".join(a.full_name() for a in authors)
    return f"{title} {author_names} {year or ''}".strip()


def _score(query: str, title: str, authors: list, year) -> float:
    signature = _build_signature(title, authors, year)
    return fuzz.token_set_ratio(_normalize(query), _normalize(signature))


def score_against_query(query: str, title: str, authors: list, year) -> float:
    """Public entry point for scoring an arbitrary title/authors/year against
    a raw reference string. Used by comparator.py to check whether a DOI's
    resolved metadata actually matches the reference it was attached to."""
    return _score(query, title, authors, year)


def _request_with_retries(url: str, params: dict, headers: dict):
    """Shared retry/backoff logic. Returns (response, None) on success or
    (None, error_message) after exhausting retries. Never raises."""
    last_error = "Unknown error."
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.Timeout:
            last_error = "Request timed out."
        except requests.exceptions.ConnectionError:
            last_error = "Could not connect (network issue)."
        except requests.exceptions.RequestException as exc:
            last_error = f"Unexpected request error: {exc}"
        else:
            if response.status_code == 200:
                return response, None
            elif response.status_code == 429:
                last_error = "Rate limited (429)."
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            else:
                last_error = f"Unexpected status {response.status_code}."

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    return None, f"Failed after {MAX_RETRIES} attempts. Last error: {last_error}"


def _doiresult_to_candidate(doi_result, score: float, source: str) -> Candidate:
    return Candidate(
        source=source,
        doi=doi_result.doi,
        title=doi_result.title,
        authors=doi_result.authors,
        year=doi_result.year,
        journal=doi_result.journal,
        volume=doi_result.volume,
        issue=doi_result.issue,
        pages=doi_result.pages,
        score=score,
        url=doi_result.url,
    )


def parse_openalex_work(work: dict) -> Candidate:
    """Turn a raw OpenAlex 'work' object into a Candidate (score filled in by the caller)."""
    title = (work.get("title") or work.get("display_name") or "").strip()
    year = work.get("publication_year")

    doi_raw = work.get("doi") or ""
    doi = clean_doi(doi_raw) if doi_raw else ""

    authors = []
    for authorship in work.get("authorships") or []:
        author_obj = authorship.get("author") or {}
        display_name = (author_obj.get("display_name") or "").strip()
        if not display_name:
            continue
        parts = display_name.split()
        if len(parts) >= 2:
            given, family = " ".join(parts[:-1]), parts[-1]
        else:
            given, family = "", display_name
        authors.append(Author(given=given, family=family))

    primary_location = work.get("primary_location") or {}
    source_info = primary_location.get("source") or {}
    journal = (source_info.get("display_name") or "").strip()

    biblio = work.get("biblio") or {}
    volume = (biblio.get("volume") or "").strip()
    issue = (biblio.get("issue") or "").strip()
    first_page = biblio.get("first_page")
    last_page = biblio.get("last_page")
    pages = f"{first_page}-{last_page}" if first_page and last_page else (first_page or "")

    return Candidate(
        source="openalex",
        doi=doi,
        title=title,
        authors=authors,
        year=year,
        journal=journal,
        volume=volume,
        issue=issue,
        pages=pages,
        score=0.0,
        url=work.get("id", ""),
    )


def _search_crossref(query: str, mailto: str, rows: int):
    params = {"query.bibliographic": query, "rows": rows, "mailto": mailto}
    headers = {"User-Agent": f"ReferenceChecker/1.0 (mailto:{mailto})"}
    response, error = _request_with_retries(CROSSREF_SEARCH_URL, params, headers)
    if error:
        return [], error

    try:
        payload = response.json()
    except ValueError:
        return [], "Crossref search returned a response that wasn't valid JSON."

    items = (payload.get("message") or {}).get("items") or []
    candidates = []
    for item in items:
        doi = item.get("DOI", "")
        doi_result = parse_crossref_work(item, doi)
        score = _score(query, doi_result.title, doi_result.authors, doi_result.year)
        candidates.append(_doiresult_to_candidate(doi_result, score, "crossref"))
    return candidates, None


def _search_openalex(query: str, mailto: str, rows: int):
    params = {"search": query, "per-page": rows, "mailto": mailto}
    headers = {"User-Agent": f"ReferenceChecker/1.0 (mailto:{mailto})"}
    response, error = _request_with_retries(OPENALEX_SEARCH_URL, params, headers)
    if error:
        return [], error

    try:
        payload = response.json()
    except ValueError:
        return [], "OpenAlex search returned a response that wasn't valid JSON."

    results = payload.get("results") or []
    candidates = []
    for work in results:
        candidate = parse_openalex_work(work)
        candidate.score = _score(query, candidate.title, candidate.authors, candidate.year)
        candidates.append(candidate)
    return candidates, None


def search_reference(raw_reference: str, mailto: str, rows: int = 5) -> BiblioSearchResult:
    """
    Search for the real record behind a reference that has no DOI (or whose
    DOI lookup failed). Tries Crossref first; only calls OpenAlex if Crossref
    didn't already produce a high-confidence match, to save an API call.
    """
    if not mailto or "@" not in mailto:
        raise ValueError(
            "mailto must be a real contact email. Both Crossref's and "
            "OpenAlex's polite pools require it."
        )

    query = (raw_reference or "").strip()
    if not query:
        return BiblioSearchResult(status="invalid_input", message="No reference text provided.")

    candidates, crossref_error = _search_crossref(query, mailto, rows)
    best_score_so_far = max((c.score for c in candidates), default=0)

    openalex_error = None
    if best_score_so_far < HIGH_CONFIDENCE_THRESHOLD:
        oa_candidates, openalex_error = _search_openalex(query, mailto, rows)
        candidates.extend(oa_candidates)

    candidates.sort(key=lambda c: c.score, reverse=True)

    if candidates:
        best = candidates[0]
        if best.score >= HIGH_CONFIDENCE_THRESHOLD:
            status = "matched"
        elif best.score >= LOW_CONFIDENCE_THRESHOLD:
            status = "possible_match"
        else:
            status = "no_match"

        notes = []
        if crossref_error:
            notes.append(f"Crossref issue during search: {crossref_error}")
        if openalex_error:
            notes.append(f"OpenAlex issue during search: {openalex_error}")

        return BiblioSearchResult(
            status=status,
            message=" ".join(notes),
            query=query,
            best_candidate=best,
            all_candidates=candidates[:rows],
        )

    # No candidates from either source.
    if crossref_error and openalex_error:
        return BiblioSearchResult(
            status="error",
            query=query,
            message=f"Both lookups failed. Crossref: {crossref_error} OpenAlex: {openalex_error}",
        )

    if crossref_error or openalex_error:
        # Only one source could actually be checked. The other coming back
        # empty is not a completed negative check, so this must not be
        # reported as "no_match" -- that would risk a false hallucination flag.
        err = crossref_error or openalex_error
        return BiblioSearchResult(
            status="error",
            query=query,
            message=f"Only one source could be checked; result is unverified, not confirmed absent. Error: {err}",
        )

    return BiblioSearchResult(
        status="no_match",
        query=query,
        message="No matching record found in Crossref or OpenAlex.",
    )
