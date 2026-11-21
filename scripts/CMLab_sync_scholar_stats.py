#!/usr/bin/env python3
"""Synchronize Jihyong Oh's Google Scholar profile metrics via SerpApi.

Requires the SERPAPI_API_KEY environment variable, stored only in GitHub Actions
Secrets. Outputs data/scholar-stats.js for a Google Sites HTML embed.

The script does not call the undocumented Google Scholar endpoints itself.
On an API error, it leaves the previous published data untouched.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUTHOR_ID = "D5yPXMsAAAAJ"
PROFILE_URL = f"https://scholar.google.com/citations?user={AUTHOR_ID}&hl=en"
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "data" / "scholar-stats.js"
API_ENDPOINT = "https://serpapi.com/search.json"


def positive_int(raw: Any, field: str) -> int:
    if isinstance(raw, bool):
        raise ValueError(f"{field} must be numeric, not boolean")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and re.fullmatch(r"\s*[0-9][0-9,]*\s*", raw):
        value = int(raw.strip().replace(",", ""))
    else:
        raise ValueError(f"{field} must be an integer or digits string")
    if not (0 <= value <= 100_000_000):
        raise ValueError(f"{field} is outside expected range")
    return value


def parse_scholar_payload(payload: dict[str, Any]) -> dict[str, int]:
    if payload.get("error"):
        raise ValueError("SerpApi returned an error instead of citation statistics")
    params = payload.get("search_parameters", {})
    if isinstance(params, dict):
        returned_id = params.get("author_id")
        if returned_id is not None and returned_id != AUTHOR_ID:
            raise ValueError("Scholar author ID did not match the configured PI")
    metadata = payload.get("search_metadata", {})
    if isinstance(metadata, dict) and metadata.get("status") == "Error":
        raise ValueError("SerpApi search status was Error")
    author = payload.get("author", {})
    if isinstance(author, dict) and author.get("name"):
        name = str(author["name"]).lower()
        if "jihyong" not in name or "oh" not in name:
            raise ValueError("Scholar author name does not match the configured PI")

    cited_by = payload.get("cited_by", {})
    if not isinstance(cited_by, dict) or not isinstance(cited_by.get("table"), list):
        raise ValueError("Missing cited_by.table; profile may be blocked or not public")

    all_citations = None
    all_hindex = None
    for row in cited_by["table"]:
        if not isinstance(row, dict):
            continue
        citations = row.get("citations")
        hindex = row.get("h_index")
        if isinstance(citations, dict) and "all" in citations:
            all_citations = positive_int(citations["all"], "citations.all")
        if isinstance(hindex, dict) and "all" in hindex:
            all_hindex = positive_int(hindex["all"], "h_index.all")

    if all_citations is None or all_hindex is None:
        raise ValueError("The citation or h-index All-time field was missing")
    if all_hindex > all_citations:
        raise ValueError("Invalid Scholar metrics: h-index exceeds citation count")
    return {"citations": all_citations, "h_index": all_hindex}


def fetch_serpapi(api_key: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({
        "engine": "google_scholar_author",
        "author_id": AUTHOR_ID,
        "hl": "en",
        "api_key": api_key,
    })
    request = urllib.request.Request(
        API_ENDPOINT + "?" + query,
        headers={"Accept": "application/json", "User-Agent": "CMLab-Scholar-Metrics/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(4_000_000)
    except urllib.error.HTTPError as exc:
        # Do not print the exception or the request URL: they contain the key.
        raise RuntimeError(f"SerpApi HTTP {exc.code}; verify key, credits, and provider status") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("SerpApi connection failed; previous statistics remain unchanged") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise RuntimeError("SerpApi response is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("SerpApi returned unexpected JSON type")
    return data


def existing_metrics(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        match = re.search(r"\bvar stats = (\{[^\n]*\});", content)
        if not match:
            return None
        data = json.loads(match.group(1))
        return {"citations": data.get("citations"), "h_index": data.get("h_index")}
    except (OSError, json.JSONDecodeError):
        return None


def write_stats(metrics: dict[str, int], output: Path) -> bool:
    if existing_metrics(output) == metrics:
        print("Google Scholar metrics unchanged; retaining existing published timestamp.", flush=True)
        return False
    payload = {
        **metrics,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": PROFILE_URL,
        "scholar_author_id": AUTHOR_ID,
    }
    # No untrusted HTML or user input is inserted into script source.
    js = (
        "/* Generated CMLab Scholar statistics. No API key is stored here. */\n"
        "(function(){\n"
        "  var stats = " + json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + ";\n"
        "  window.CMLAB_SCHOLAR_STATS = stats;\n"
        "  if (typeof window.cmlabScholarStatsApply === 'function') "
        "window.cmlabScholarStatsApply(stats);\n"
        "}());\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(js, encoding="utf-8")
    print(f"Updated {output}", flush=True)
    return True


def main() -> int:
    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: Set repository secret SERPAPI_API_KEY. Previous statistics not changed.", file=sys.stderr)
        return 1
    try:
        metrics = parse_scholar_payload(fetch_serpapi(api_key))
        print(f"Validated Google Scholar metrics for {AUTHOR_ID}: {metrics}", flush=True)
        write_stats(metrics, OUTPUT)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: Scholar synchronization failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
