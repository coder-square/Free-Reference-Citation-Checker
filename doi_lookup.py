"""
doi_lookup.py

Looks up a DOI against the Crossref REST API and returns verified
bibliographic metadata: title, authors, year, journal, volume, issue, pages.

No API key required. Uses Crossref's "polite pool" via a mailto parameter,
which gets faster and more reliable service than anonymous requests.

Every possible outcome returns an explicit status -- nothing fails silently:
  "found"        - DOI resolved, metadata below is real
  "not_found"    - DOI does not exist in Crossref (may be wrong or fabricated)
  "invalid_doi"  - input wasn't a DOI-shaped string at all
  "error"        - network/server problem after retries; NOT the same as
                   "not_found" and must never be treated as one
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

CROSSREF_BASE_URL = "https://api.crossref.org/works/"
REQUEST_TIMEOUT = 10  # seconds per attempt
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2

# Standard DOI syntax: 10.NNNN(.NNNN)/suffix
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")


@dataclass
class Author:
    given: str = ""
    family: str = ""

    def full_name(self) -> str:
        if self.family and self.given:
            return f"{self.family}, {self.given}"
        return self.family or self.given or ""


@dataclass
class DoiResult:
    status: str  # "found" | "not_found" | "invalid_doi" | "error"
    message: str = ""
    doi: str = ""
    title: str = ""
    authors: list = field(default_factory=list)
    year: Optional[int] = None
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    url: str = ""


def clean_doi(raw: str) -> str:
    """Strip common prefixes/whitespace so a full doi.org link or bare DOI both work."""
    if not raw:
        return ""
    doi = raw.strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.strip().strip(".").strip()


def is_valid_doi_format(doi: str) -> bool:
    return bool(DOI_PATTERN.match(doi))


def _extract_authors(work: dict) -> list:
    authors = []
    for a in work.get("author", []) or []:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        if given or family:
            authors.append(Author(given=given, family=family))
    return authors


def _extract_year(work: dict) -> Optional[int]:
    # Crossref stores dates under different fields depending on publication type;
    # check them in order of reliability.
    for date_field in ("published-print", "published-online", "published", "issued"):
        block = work.get(date_field)
        if not block:
            continue
        date_parts = block.get("date-parts")
        if date_parts and date_parts[0] and date_parts[0][0]:
            try:
                return int(date_parts[0][0])
            except (ValueError, TypeError):
                continue
    return None


def _extract_journal(work: dict) -> str:
    container = work.get("container-title")
    if container and isinstance(container, list) and container:
        return container[0].strip()
    return ""


def parse_crossref_work(work: dict, doi: str) -> DoiResult:
    """Turn a raw Crossref 'work' object into a clean, typed DoiResult."""
    title_list = work.get("title") or []
    title = title_list[0].strip() if title_list else ""

    return DoiResult(
        status="found",
        doi=doi,
        title=title,
        authors=_extract_authors(work),
        year=_extract_year(work),
        journal=_extract_journal(work),
        volume=(work.get("volume") or "").strip(),
        issue=(work.get("issue") or "").strip(),
        pages=(work.get("page") or "").strip(),
        url=work.get("URL", f"https://doi.org/{doi}"),
    )


def get_doi_metadata(raw_doi: str, mailto: str) -> DoiResult:
    """
    Look up a DOI against Crossref and return verified metadata.

    raw_doi : a DOI or a full doi.org link, in any reasonable format.
    mailto  : your real contact email. Required -- Crossref's polite pool
              needs it for reliable service, and this function refuses to
              run without one rather than silently degrading.
    """
    if not mailto or "@" not in mailto:
        raise ValueError(
            "mailto must be a real contact email. Crossref's polite pool "
            "requires it; results are slower and less reliable without it."
        )

    doi = clean_doi(raw_doi)

    if not doi:
        return DoiResult(status="invalid_doi", message="No DOI provided.")

    if not is_valid_doi_format(doi):
        return DoiResult(
            status="invalid_doi",
            doi=doi,
            message=f"'{doi}' does not match standard DOI format (10.NNNN/suffix).",
        )

    url = f"{CROSSREF_BASE_URL}{doi}"
    headers = {"User-Agent": f"ReferenceChecker/1.0 (mailto:{mailto})"}
    params = {"mailto": mailto}

    last_error = "Unknown error."
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.Timeout:
            last_error = "Request to Crossref timed out."
        except requests.exceptions.ConnectionError:
            last_error = "Could not connect to Crossref (network issue)."
        except requests.exceptions.RequestException as exc:
            last_error = f"Unexpected request error: {exc}"
        else:
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    last_error = "Crossref returned a response that wasn't valid JSON."
                else:
                    work = payload.get("message") or {}
                    if not work:
                        return DoiResult(
                            status="not_found",
                            doi=doi,
                            message="Crossref returned an empty record for this DOI.",
                        )
                    return parse_crossref_work(work, doi)

            elif response.status_code == 404:
                return DoiResult(
                    status="not_found",
                    doi=doi,
                    message=(
                        "This DOI does not exist in Crossref. It may be mistyped, "
                        "unregistered, or fabricated -- verify manually before "
                        "flagging it as fake."
                    ),
                )

            elif response.status_code == 429:
                last_error = "Rate limited by Crossref (429)."
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            else:
                last_error = f"Crossref returned unexpected status {response.status_code}."

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    # Every retry failed. This is explicitly NOT "not_found" -- it means
    # we couldn't reach a verdict at all, and the caller must not treat
    # it as evidence the reference is fabricated.
    return DoiResult(
        status="error",
        doi=doi,
        message=f"Failed after {MAX_RETRIES} attempts. Last error: {last_error}",
    )
