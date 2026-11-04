"""CSV export for Facebook Pages found by Find Pages."""
from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

PAGE_CSV_FIELDS = ("stt", "page_name", "page_url", "page_id", "category", "followers", "data_source")


def export_pages_csv(path: str | Path, pages: Iterable[Mapping[str, Any]]) -> int:
    """Write unique pages (by URL) as UTF-8 BOM CSV; return number of rows written."""
    seen: set[str] = set()
    rows: list[list[str]] = []
    for page in pages:
        url = str(page.get("page_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append([str(len(rows) + 1)] + [str(page.get(field) or "") for field in PAGE_CSV_FIELDS[1:]])
        rows[-1][2] = url
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(PAGE_CSV_FIELDS)
        writer.writerows(rows)
    return len(rows)