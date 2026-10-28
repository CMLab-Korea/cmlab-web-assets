#!/usr/bin/env python3
"""Extract CMLab publication counts from the *rendered* Google Sites page.

Run from repository root. Outputs data/publication-stats.js, consumed by
Google Sites' existing CMLab Hero embed. Never publishes guessed numbers.
"""

from __future__ import annotations

import html
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

URL = "https://cmlab.cau.ac.kr/publications"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPOSITORY_ROOT / "data" / "publication-stats.js"

def normalize(text: str) -> str:
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_counts(page_text: str) -> dict[str, int]:
    text = normalize(page_text)
    summary = re.search(r"\bSummary\s*:\s*(.{0,1100})", text, re.IGNORECASE)
    if not summary:
        raise ValueError("Could not locate 'Summary:' in the rendered Publications page.")
    passage = summary.group(1)

    patterns = {
        "total": r"\b(\d+)\s+peer\s*-\s*reviewed\s+international\s+publications\b",
        "conference": r"\b(\d+)\s+top\s*-\s*tier\s+AI\s*/\s*CV\s+conference\s+papers\b",
        "journal": r"\b(\d+)\s+international\s+journal\s+papers\b",
        "preprint": r"\b(\d+)\s+additional\s+manuscripts\s+are\s+currently\s+under\s+review\b",
    }
    values: dict[str, int] = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, passage, flags=re.IGNORECASE)
        if m is None:
            raise ValueError(f"Summary found, but '{key}' count was not identified. Summary excerpt: {passage[:360]!r}")
        values[key] = int(m.group(1))

    if values["total"] != values["conference"] + values["journal"]:
        raise ValueError(f"Inconsistent Summary counts: {values}")
    if not all(0 <= number <= 10000 for number in values.values()):
        raise ValueError(f"Publication numbers out of expected range: {values}")
    return values


def read_rendered_page() -> str:
    # Google Sites may render its content through JavaScript: HTTP-only parsers
    # are not guaranteed to see the same text as a browser.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
            response = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            status = response.status if response else "unknown"
            print(f"Loaded Publications: HTTP {status}; title={page.title()!r}", flush=True)
            if response and response.status >= 400:
                raise RuntimeError(f"Publications page HTTP {response.status}")

            # The publication Summary may be rendered asynchronously.
            page.wait_for_function(
                "() => /Summary\\s*:/i.test(document.body?.innerText || '')",
                timeout=45000,
            )
            return page.locator("body").inner_text(timeout=15000)
        finally:
            browser.close()


def old_counts(output: Path) -> dict[str, int] | None:
    if not output.exists():
        return None
    old = output.read_text(encoding="utf-8")
    m = re.search(r"\bvar\s+stats\s*=\s*(\{[^\n]*\})\s*;", old)
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return {key: payload.get(key) for key in ("total", "conference", "journal", "preprint")}


def main() -> int:
    print(f"Reading rendered text from {URL}", flush=True)
    try:
        text = read_rendered_page()
        print(f"Extracted {len(text)} characters of rendered page text.", flush=True)
        counts = extract_counts(text)
    except Exception as exc:
        # Log a concise error without rewriting a previously valid stats file.
        print(f"ERROR: Could not parse Publications Summary. {exc}", file=sys.stderr)
        return 1

    print(f"Validated publication counts: {counts}", flush=True)
    if old_counts(OUTPUT) == counts:
        print("Statistics unchanged; keeping existing file and timestamp.", flush=True)
        return 0

    stats = {
        **counts,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": URL,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "/* Generated from the rendered CMLab Publications Summary. */\n"
        "(function(){\n"
        "  var stats = " + json.dumps(stats, ensure_ascii=True) + ";\n"
        "  window.CMLAB_PUBLICATION_STATS = stats;\n"
        '  if (typeof window.cmlabStatsApply === "function") window.cmlabStatsApply(stats);\n'
        "}());\n",
        encoding="utf-8",
    )
    print(f"Updated {OUTPUT.as_posix()}", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
