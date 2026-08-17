"""
comparator.py

The core decision-making layer. For each individual reference string, this
module:
  1. Looks for a DOI embedded in the text and verifies it directly if found.
  2. If there's no DOI, or the DOI doesn't verify, or it resolves to a
     DIFFERENT work than the text describes (a strong fabrication/mismatch
     signal), falls back to bibliographic search on the reference text.
  3. Compares the verified record's authors, year, and volume/issue against
     what's actually written in the reference, and flags every difference.
  4. Checks author-initials formatting for consistency across the whole list.
  5. Produces a corrected, properly formatted entry from the verified data.

Every reference lands in exactly one of these statuses -- never ungraded:
  "verified"                 - matched with high confidence, all checked
                                fields agree
  "verified_with_corrections"- matched with high confidence, but one or
                                more fields differed or were filled in
  "possible_mismatch"        - only a moderate-confidence match found;
                                needs a human to confirm
  "flagged_unverifiable"     - checked cleanly against every available
                                source and nothing usable turned up; may
                                be fabricated, may simply be an obscure or
                                non-indexed source (book, some conference
                                proceedings) -- always phrased as "needs
                                manual verification", never as a confirmed
                                fabrication
  "unable_to_verify"         - a network/technical problem prevented a
                                complete check; MUST NOT be treated the
                                same as flagged_unverifiable
  "invalid"                  - empty input
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from doi_lookup import (
    Author,
    clean_doi,
    is_valid_doi_format,
    get_doi_metadata,
)
from biblio_search import (
    search_reference,
    score_against_query,
    LOW_CONFIDENCE_THRESHOLD,
)

DOI_IN_TEXT_PATTERN = re.compile(r"(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/[^\s,;]+", re.IGNORECASE)
YEAR_IN_PARENS_PATTERN = re.compile(r"\((\d{4})[a-z]?\)")
BARE_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
VOLUME_ISSUE_PAREN_PATTERN = re.compile(r"\b(\d{1,4})\s*\((\d{1,3}[a-zA-Z]?)\)")
VOLUME_ISSUE_WORDED_PATTERN = re.compile(
    r"[Vv]ol(?:ume)?\.?\s*(\d{1,4}).{0,15}?[Nn]o\.?\s*(\d{1,3})"
)


@dataclass
class ReferenceCheckResult:
    original_text: str
    status: str
    issues: list = field(default_factory=list)
    corrected_entry: str = ""
    source: str = ""
    confidence: Optional[float] = None
    verified_doi: str = ""


# ---------- extraction from raw reference text ----------

def extract_doi_from_text(text: str) -> Optional[str]:
    match = DOI_IN_TEXT_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(0).rstrip(".,;)")
    cleaned = clean_doi(raw)
    return cleaned if is_valid_doi_format(cleaned) else None


def extract_year_from_text(text: str) -> Optional[int]:
    match = YEAR_IN_PARENS_PATTERN.search(text)
    if match:
        return int(match.group(1))
    match = BARE_YEAR_PATTERN.search(text)
    if match:
        return int(match.group(0))
    return None


def extract_volume_issue_from_text(text: str):
    match = VOLUME_ISSUE_PAREN_PATTERN.search(text)
    if match:
        return match.group(1), match.group(2)
    match = VOLUME_ISSUE_WORDED_PATTERN.search(text)
    if match:
        return match.group(1), match.group(2)
    return None, None


# ---------- author / initials checks ----------

def compare_authors_to_text(verified_authors: list, raw_text: str) -> list:
    """Flag any verified-record author whose surname doesn't appear anywhere
    in the reference text. Can only check for absence, not extra/wrong
    authors, since the raw text isn't structured into fields."""
    issues = []
    normalized = raw_text.lower()
    for a in verified_authors:
        if a.family and a.family.lower() not in normalized:
            issues.append(
                f"Author '{a.family}' appears in the verified record but "
                f"was not found in the reference text."
            )
    return issues


def detect_initials_style(raw_text: str) -> str:
    """Classify the author-formatting style used at the start of a reference."""
    snippet = raw_text.strip()[:80]
    if re.match(r"^[A-Z][a-zA-Z\-']+,\s*[A-Z]\.(\s*[A-Z]\.)*", snippet):
        return "surname_comma_initials"  # Smith, J. M.
    if re.match(r"^([A-Z]\.\s*){1,3}[A-Z][a-zA-Z\-']+", snippet):
        return "initials_first"  # J. M. Smith
    if re.match(r"^[A-Z][a-zA-Z\-']+\s+[A-Z]{1,3}\b", snippet):
        return "surname_no_periods"  # Smith JM
    return "unknown"


def check_initials_consistency(raw_entries: list) -> dict:
    """Flag entries whose author-formatting style differs from the list's
    dominant style. This is a formatting/consistency check, separate from
    whether the reference itself is correct."""
    styles = [detect_initials_style(e) for e in raw_entries]
    known = [s for s in styles if s != "unknown"]
    if not known:
        return {"dominant_style": "unknown", "styles": styles, "flagged_indices": []}
    dominant = Counter(known).most_common(1)[0][0]
    flagged = [i for i, s in enumerate(styles) if s != "unknown" and s != dominant]
    return {"dominant_style": dominant, "styles": styles, "flagged_indices": flagged}


# ---------- formatting a corrected entry ----------

def format_authors_apa(authors: list) -> str:
    formatted = []
    for a in authors:
        initials = "".join(f"{part[0]}." for part in a.given.split() if part) if a.given else ""
        if a.family and initials:
            formatted.append(f"{a.family}, {initials}")
        elif a.family:
            formatted.append(a.family)
    if not formatted:
        return ""
    if len(formatted) == 1:
        result = formatted[0]
    elif len(formatted) == 2:
        result = f"{formatted[0]}, & {formatted[1]}"
    else:
        result = ", ".join(formatted[:-1]) + f", & {formatted[-1]}"
    # Initials already end in a period ("T.M."); a bare surname doesn't.
    # Add a trailing period only when one isn't already there, to avoid "T.M.."
    return result if result.endswith(".") else result + "."


def format_apa_reference(title, authors, year, journal, volume, issue, pages, doi) -> str:
    parts = [format_authors_apa(authors)]
    parts.append(f"({year})." if year else "(n.d.).")
    if title:
        parts.append(f"{title}.")
    if journal:
        journal_part = journal
        if volume:
            journal_part += f", {volume}"
            if issue:
                journal_part += f"({issue})"
        if pages:
            journal_part += f", {pages}"
        parts.append(journal_part + ".")
    if doi:
        parts.append(f"https://doi.org/{doi}")
    return " ".join(p for p in parts if p).strip()


# ---------- field comparison ----------

def _compare_fields_and_build_correction(raw_text: str, record) -> tuple:
    issues = list(compare_authors_to_text(record.authors, raw_text))

    text_year = extract_year_from_text(raw_text)
    if record.year and text_year and text_year != record.year:
        issues.append(f"Year mismatch: reference states {text_year}, verified record shows {record.year}.")
    elif record.year and not text_year:
        issues.append(f"No year found in the reference text; verified record shows {record.year}.")

    text_volume, text_issue = extract_volume_issue_from_text(raw_text)

    if record.volume and not text_volume:
        issues.append(f"Volume number missing -- added from verified record: {record.volume}.")
    elif record.volume and text_volume and str(text_volume) != str(record.volume):
        issues.append(f"Volume mismatch: reference states {text_volume}, verified record shows {record.volume}.")

    if record.issue and not text_issue:
        issues.append(f"Issue number missing -- added from verified record: {record.issue}.")
    elif record.issue and text_issue and str(text_issue) != str(record.issue):
        issues.append(f"Issue mismatch: reference states {text_issue}, verified record shows {record.issue}.")

    corrected = format_apa_reference(
        title=record.title,
        authors=record.authors,
        year=record.year,
        journal=record.journal,
        volume=record.volume,
        issue=record.issue,
        pages=record.pages,
        doi=record.doi,
    )
    return issues, corrected


# ---------- main entry point ----------

def check_reference(raw_text: str, mailto: str) -> ReferenceCheckResult:
    text = (raw_text or "").strip()
    if not text:
        return ReferenceCheckResult(original_text=raw_text or "", status="invalid", issues=["Empty reference text."])

    issues = []
    record = None
    source = ""
    confidence = None
    status = None

    doi_in_text = extract_doi_from_text(text)

    if doi_in_text:
        doi_result = get_doi_metadata(doi_in_text, mailto=mailto)

        if doi_result.status == "found":
            score = score_against_query(text, doi_result.title, doi_result.authors, doi_result.year)
            if score >= LOW_CONFIDENCE_THRESHOLD:
                record, source, confidence = doi_result, "crossref_doi", score
            else:
                issues.append(
                    f'The DOI in this reference resolves to a different work: "{doi_result.title}". '
                    "This DOI may be mistyped or attached to the wrong reference -- verify manually."
                )
        elif doi_result.status == "not_found":
            issues.append("The DOI given does not exist in Crossref -- likely mistyped or fabricated.")
        elif doi_result.status == "error":
            issues.append(f"Could not verify the DOI due to a connection problem: {doi_result.message}")
        # "invalid_doi" needs no note here -- fall through to bibliographic search silently.

    if record is None:
        biblio = search_reference(text, mailto=mailto)

        if biblio.status == "matched":
            record = biblio.best_candidate
            source = f"{biblio.best_candidate.source}_search"
            confidence = biblio.best_candidate.score
        elif biblio.status == "possible_match":
            record = biblio.best_candidate
            source = f"{biblio.best_candidate.source}_search"
            confidence = biblio.best_candidate.score
            status = "possible_mismatch"
        elif biblio.status == "no_match":
            status = "flagged_unverifiable"
        else:  # "error"
            status = "unable_to_verify"

        if biblio.message:
            issues.append(biblio.message)

    if record is None:
        return ReferenceCheckResult(
            original_text=text,
            status=status or "unable_to_verify",
            issues=issues,
            source=source,
        )

    field_issues, corrected = _compare_fields_and_build_correction(text, record)
    issues.extend(field_issues)

    if status is None:
        status = "verified_with_corrections" if field_issues else "verified"

    return ReferenceCheckResult(
        original_text=text,
        status=status,
        issues=issues,
        corrected_entry=corrected,
        source=source,
        confidence=confidence,
        verified_doi=record.doi,
    )


def check_reference_list(raw_entries: list, mailto: str) -> dict:
    """Check every reference in a list and add a list-level initials
    consistency check. Note: this makes network calls sequentially, one
    per reference -- fine for typical bibliography sizes, but very large
    lists (100+) will take a while and should eventually be rate-limit-aware."""
    results = [check_reference(entry, mailto) for entry in raw_entries]
    consistency = check_initials_consistency(raw_entries)
    return {"results": results, "initials_consistency": consistency}
