"""
citation_styles.py

Per-reference citation-style detection and formatting.

Verification (DOI/Crossref/OpenAlex matching) in comparator.py and
biblio_search.py is style-agnostic -- it works on the reference text
regardless of format. This module only affects how the *corrected*
entry is written back out: once a reference is verified, we detect
which style the original entry was written in and reformat the
corrected data to match that same style, instead of forcing everything
into APA.

Supported styles: "apa", "mla", "chicago", "harvard", "vancouver".

This is heuristic, not a full citation-style validator. It looks at a
handful of strong, distinguishing signals in the raw entry text --
numbering, quote style, where the year sits -- rather than implementing
a complete grammar for each style. When nothing matches confidently, it
falls back to "apa" as a conservative default rather than guessing.
"""

import re

VANCOUVER_NUMBERED = re.compile(r"^\s*\d{1,3}[.)]\s")
MLA_TITLE_SIGNAL = re.compile(r'"[^"]{5,}"')          # "Title." in double quotes
HARVARD_TITLE_SIGNAL = re.compile(r"'[^']{5,}'")       # 'Title' in single quotes
APA_STYLE_SIGNAL = re.compile(r"^[A-Z][a-zA-Z\-']+,\s*[A-Z]\.(\s*[A-Z]\.)*\s*\(\d{4}\)")
CHICAGO_AUTHOR_DATE_SIGNAL = re.compile(r"^[A-Z][a-zA-Z\-']+,\s+[A-Z][a-zA-Z]+\.\s+\d{4}\.")


def detect_citation_style(raw_text: str) -> str:
    """Guess which citation style a single raw reference entry was written in."""
    text = (raw_text or "").strip()
    if not text:
        return "apa"

    if VANCOUVER_NUMBERED.match(text):
        return "vancouver"

    # Checked before MLA: both use quoted titles, but Chicago's leading
    # "Surname, First Name. Year." shape is the more specific signal, so it
    # takes priority over the more general quote+vol/no MLA check below.
    if CHICAGO_AUTHOR_DATE_SIGNAL.match(text):
        return "chicago"

    if MLA_TITLE_SIGNAL.search(text) and re.search(r"\bvol\.|\bno\.", text, re.IGNORECASE):
        return "mla"

    if HARVARD_TITLE_SIGNAL.search(text) and re.search(r"\bpp\.", text, re.IGNORECASE):
        return "harvard"

    if APA_STYLE_SIGNAL.match(text):
        return "apa"

    return "apa"


def _authors_full_surname_first(authors: list) -> list:
    """['Fasuan, Titilope M.', 'Bello, Kunle'] using the full given name if
    we have one, else falling back to whatever initials we do have."""
    names = []
    for a in authors:
        if a.family and a.given:
            names.append(f"{a.family}, {a.given}")
        elif a.family:
            names.append(a.family)
    return names


def _join_authors(names: list, final_sep: str = "and") -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}, {final_sep} {names[1]}"
    return ", ".join(names[:-1]) + f", {final_sep} {names[-1]}"


def format_authors_apa(authors: list) -> str:
    """Kept here so every style formatter lives in one place. comparator.py
    still keeps its own copy of this exact function for backward
    compatibility with existing tests/imports -- the two must stay identical."""
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
    return result if result.endswith(".") else result + "."


def format_apa(title, authors, year, journal, volume, issue, pages, doi) -> str:
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


def format_mla(title, authors, year, journal, volume, issue, pages, doi) -> str:
    names = _authors_full_surname_first(authors)
    author_part = _join_authors(names, final_sep="and")
    parts = [f"{author_part}." if author_part else ""]
    if title:
        parts.append(f'"{title}."')
    if journal:
        parts.append(f"{journal},")
    if volume:
        parts.append(f"vol. {volume},")
    if issue:
        parts.append(f"no. {issue},")
    if year:
        parts.append(f"{year},")
    if pages:
        parts.append(f"pp. {pages}.")
    if doi:
        parts.append(f"https://doi.org/{doi}.")
    result = " ".join(p for p in parts if p).strip()
    return re.sub(r",\s*\.", ".", result)  # tidy a stray ", ." when pages/doi are absent


def format_chicago(title, authors, year, journal, volume, issue, pages, doi) -> str:
    names = _authors_full_surname_first(authors)
    author_part = _join_authors(names, final_sep="and")
    parts = [f"{author_part}." if author_part else ""]
    if year:
        parts.append(f"{year}.")
    if title:
        parts.append(f'"{title}."')
    if journal:
        journal_part = journal
        if volume:
            journal_part += f" {volume}"
            if issue:
                journal_part += f", no. {issue}"
        parts.append(journal_part + (":" if pages else "."))
    if pages:
        parts.append(f"{pages}.")
    if doi:
        parts.append(f"https://doi.org/{doi}.")
    return " ".join(p for p in parts if p).strip()


def format_harvard(title, authors, year, journal, volume, issue, pages, doi) -> str:
    names = _authors_full_surname_first(authors)
    author_part = _join_authors(names, final_sep="and")
    parts = [author_part if author_part else ""]
    if year:
        parts.append(f"({year})")
    if title:
        parts.append(f"'{title}',")
    if journal:
        journal_part = journal
        if volume:
            journal_part += f", {volume}"
            if issue:
                journal_part += f"({issue})"
        parts.append(journal_part + ",")
    if pages:
        parts.append(f"pp. {pages}.")
    if doi:
        parts.append(f"https://doi.org/{doi}.")
    result = " ".join(p for p in parts if p).strip()
    return re.sub(r",\s*\.", ".", result)


def format_vancouver(title, authors, year, journal, volume, issue, pages, doi) -> str:
    # Vancouver initials are unspaced and unpunctuated: "Fasuan TM".
    names = []
    for a in authors:
        initials = "".join(part[0] for part in a.given.split() if part) if a.given else ""
        if a.family:
            names.append(f"{a.family} {initials}".strip())
    author_part = ", ".join(names)
    parts = [f"{author_part}." if author_part else ""]
    if title:
        parts.append(f"{title}.")
    if journal:
        parts.append(f"{journal}.")
    if year:
        cite = f"{year}"
        if volume:
            cite += f";{volume}"
            if issue:
                cite += f"({issue})"
        if pages:
            cite += f":{pages}"
        parts.append(cite + ".")
    if doi:
        parts.append(f"doi:{doi}")
    return " ".join(p for p in parts if p).strip()


STYLE_FORMATTERS = {
    "apa": format_apa,
    "mla": format_mla,
    "chicago": format_chicago,
    "harvard": format_harvard,
    "vancouver": format_vancouver,
}


def format_reference(style: str, title, authors, year, journal, volume, issue, pages, doi) -> str:
    """Format verified reference data in the given style. Unknown styles
    fall back to APA rather than raising, since a corrected entry in the
    wrong style is still far more useful than no corrected entry at all."""
    formatter = STYLE_FORMATTERS.get(style, format_apa)
    return formatter(title, authors, year, journal, volume, issue, pages, doi)
