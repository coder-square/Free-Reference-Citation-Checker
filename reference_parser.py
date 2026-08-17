"""
reference_parser.py

Splits a full reference list -- pasted text or extracted from a .docx --
into individual reference entries, ready to be checked one at a time
against Crossref/OpenAlex.

HONEST LIMITATION, stated up front: there is no universal reference-list
format. Researchers paste lists separated by blank lines, by numbers, by
nothing at all with just a period-then-capital-letter boundary, sometimes
mixed within the same document. This module tries several strategies in
order of reliability and reports which one it used and how confident it
is -- it does not silently guess and hide the guess.

Confidence order (most to least reliable):
  1. blank_line    - entries separated by blank lines (most reliable when
                      it applies -- this is also exactly what you get from
                      a Word doc where each reference is its own paragraph)
  2. numbered      - entries prefixed with "1.", "1)", or "[1]"
  3. author_pattern- fallback for continuous pasted text: split wherever
                      a new "Surname, X." pattern starts right after a
                      preceding period. This is the least reliable
                      strategy and should be flagged to the user as such.
  4. single_block  - none of the above applied; the whole input is
                      returned as one entry, with a warning. This is a
                      signal to fall back to manual splitting, not a
                      successful parse.
"""

import re
from dataclasses import dataclass, field


NUMBERED_PATTERN = re.compile(r"(?m)^\s*(?:\[\d{1,3}\]|\d{1,3}[.)])\s+")
AUTHOR_START_PATTERN = re.compile(r"(?<=\.)\s+(?=[A-Z][a-zA-Z\-']+,\s+[A-Z]\.)")
YEAR_PATTERN = re.compile(r"(19|20)\d{2}")

REFERENCE_SECTION_HEADINGS = {
    "references", "reference list", "bibliography",
    "works cited", "literature cited", "cited literature",
}


@dataclass
class SplitResult:
    entries: list = field(default_factory=list)
    method: str = ""  # "blank_line" | "numbered" | "author_pattern" | "single_block" | "empty"
    warning: str = ""


@dataclass
class DocxExtractionResult:
    text: str = ""
    found_heading: bool = False
    paragraph_count: int = 0
    warning: str = ""


def _looks_like_a_reference(block: str) -> bool:
    """A crude but useful filter: a real reference almost always has a year
    and enough length to not just be a stray blank line or page number."""
    return bool(YEAR_PATTERN.search(block)) and len(block.strip()) > 20


def _blank_line_blocks_look_valid(blocks: list) -> bool:
    if len(blocks) < 2:
        return False
    matching = sum(1 for b in blocks if _looks_like_a_reference(b))
    return matching / len(blocks) >= 0.6


def _split_by_blank_lines(text: str) -> list:
    blocks = re.split(r"\n\s*\n", text)
    return [b.strip() for b in blocks if b.strip()]


def _split_by_numbering(text: str) -> list:
    matches = list(NUMBERED_PATTERN.finditer(text))
    if len(matches) < 2:
        return []
    entries = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entry = text[start:end].strip()
        if entry:
            entries.append(entry)
    return entries


def _split_by_author_pattern(text: str) -> list:
    parts = AUTHOR_START_PATTERN.split(text)
    parts = [p.strip() for p in parts if p.strip()]
    return parts


def split_references(raw_text: str) -> SplitResult:
    """
    Split a full reference list into individual entries. Tries strategies
    in order of reliability and stops at the first one that produces a
    plausible result. Always returns a SplitResult -- check `.method` and
    `.warning` before trusting the split blindly.
    """
    text = (raw_text or "").strip()
    if not text:
        return SplitResult(entries=[], method="empty", warning="No text provided.")

    blank_blocks = _split_by_blank_lines(text)
    if _blank_line_blocks_look_valid(blank_blocks):
        return SplitResult(entries=blank_blocks, method="blank_line")

    numbered = _split_by_numbering(text)
    if len(numbered) > 1:
        return SplitResult(entries=numbered, method="numbered")

    author_split = _split_by_author_pattern(text)
    if len(author_split) > 1:
        return SplitResult(
            entries=author_split,
            method="author_pattern",
            warning=(
                "Used a fallback splitting method based on author-name "
                "patterns. This is less reliable than blank-line or "
                "numbered lists -- check the entries below for anything "
                "split incorrectly before trusting the results."
            ),
        )

    # Nothing worked. Return the whole thing as one block rather than
    # guessing wrong -- this is a signal to split it manually.
    return SplitResult(
        entries=[text],
        method="single_block",
        warning=(
            "Could not confidently split this into separate references. "
            "Returning it as a single block -- consider separating entries "
            "with blank lines or numbering them for a reliable split."
        ),
    )


def extract_text_from_docx(file_path) -> DocxExtractionResult:
    """
    Pull paragraph text from a .docx. If a heading like 'References' or
    'Bibliography' is found, only paragraphs after it are returned (so
    uploading a full manuscript doesn't drag in the whole document body).
    If no such heading is found, all non-empty paragraphs are returned,
    with a warning that the caller should confirm this is actually just
    the reference list.

    Each paragraph is treated as a separate block, joined by blank lines --
    this deliberately feeds straight into split_references' most reliable
    strategy, since Word documents almost always have one reference per
    paragraph already.
    """
    from docx import Document

    doc = Document(file_path)
    paragraphs = [p.text.strip() for p in doc.paragraphs]
    paragraphs = [p for p in paragraphs if p]

    heading_index = None
    for i, p in enumerate(paragraphs):
        normalized = p.lower().rstrip(":").strip()
        if normalized in REFERENCE_SECTION_HEADINGS:
            heading_index = i
            break

    if heading_index is not None:
        ref_paragraphs = paragraphs[heading_index + 1:]
        found_heading = True
        warning = ""
    else:
        ref_paragraphs = paragraphs
        found_heading = False
        warning = (
            "No 'References' or 'Bibliography' heading was found. "
            "Treating the entire document as reference text -- confirm "
            "this file contains only the reference list, not the full "
            "manuscript, before relying on the results."
        )

    return DocxExtractionResult(
        text="\n\n".join(ref_paragraphs),
        found_heading=found_heading,
        paragraph_count=len(ref_paragraphs),
        warning=warning,
    )
