"""
app.py

Minimal Flask front-end for the reference checker. Paste or upload a
bibliography, get back a per-reference verification report with a
corrected entry formatted in whatever citation style each entry was
originally written in (APA, MLA, Chicago, Harvard, or Vancouver --
auto-detected per reference).

NOTE ON ENTRY SPLITTING: this repo doesn't have a reference_parser.py
available to build against yet, so split_references() below uses a
simple heuristic (blank-line-separated blocks, or one reference per
line if there are no blank lines). If you have a more robust
reference_parser.py, swap the call in check() for that module's
function -- nothing else in this file needs to change, since
comparator.check_reference_list() just wants a list of raw strings.

Set REFCHECK_MAILTO to your real contact email before running --
Crossref and OpenAlex both require it for reliable service.
"""

import os
import re

from flask import Flask, render_template, request

from comparator import check_reference_list

app = Flask(__name__)

MAILTO = os.environ.get("REFCHECK_MAILTO", "you@example.com")


def split_references(raw_text: str) -> list:
    """Heuristic bibliography splitter: treats each blank-line-separated
    block as one reference, collapsing any internal line wraps. Falls back
    to one-reference-per-line if the pasted text has no blank lines at all."""
    text = (raw_text or "").replace("\r\n", "\n").strip()
    if not text:
        return []

    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) > 1:
        return [" ".join(b.split()) for b in blocks]

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return lines


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/check", methods=["POST"])
def check():
    raw_text = request.form.get("references", "")

    uploaded = request.files.get("file")
    if uploaded and uploaded.filename:
        raw_text = uploaded.read().decode("utf-8", errors="replace")

    entries = split_references(raw_text)
    if not entries:
        return render_template("index.html", error="No reference text found. Paste some text or upload a file.")

    report = check_reference_list(entries, mailto=MAILTO)
    return render_template("results.html", report=report)


if __name__ == "__main__":
    app.run(debug=True)
