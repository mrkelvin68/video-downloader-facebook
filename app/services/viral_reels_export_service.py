"""Isolated CSV, XLSX, and Google Sheets export for Viral Reels Finder."""

from __future__ import annotations

import csv
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol


class Worksheet(Protocol):
    def get_all_records(self) -> list[dict[str, Any]]: ...
    def append_rows(self, values: list[list[Any]]) -> Any: ...
    def update(self, range_name: str, values: list[list[Any]]) -> Any: ...


def _key_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return any(
        left.get(field) not in (None, "") and left.get(field) == right.get(field)
        for field in ("reel_url", "reel_id")
    )


def _merge(
    existing: Iterable[Mapping[str, Any]],
    incoming: Iterable[Mapping[str, Any]],
    update_existing: bool,
) -> tuple[list[dict[str, Any]], list[str], list[int]]:
    rows = [dict(row) for row in existing]
    changed: list[int] = []
    for item in incoming:
        record = dict(item)
        match = next((i for i, row in enumerate(rows) if _key_match(row, record)), None)
        if match is None:
            rows.append(record)
            changed.append(len(rows) - 1)
        elif update_existing:
            rows[match].update(record)
            changed.append(match)

    headers: list[str] = []
    for row in rows:
        for field in row:
            if field not in headers:
                headers.append(field)
    return rows, headers, changed


def export_csv(
    path: str | os.PathLike[str],
    records: Iterable[Mapping[str, Any]],
    *,
    update_existing: bool = False,
) -> int:
    """Append deduplicated records to CSV, optionally replacing existing metadata."""
    target = Path(path)
    existing: list[dict[str, Any]] = []
    if target.exists() and target.stat().st_size:
        with target.open("r", encoding="utf-8-sig", newline="") as stream:
            existing = list(csv.DictReader(stream))

    rows, headers, changed = _merge(existing, records, update_existing)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
        if headers:
            writer.writeheader()
            writer.writerows(rows)
    return len(changed)


def _openpyxl() -> Any:
    try:
        import openpyxl
    except ImportError as error:
        raise RuntimeError(
            "XLSX export requires optional dependency 'openpyxl'; install it in the active environment"
        ) from error
    return openpyxl


def export_xlsx(
    path: str | os.PathLike[str],
    records: Iterable[Mapping[str, Any]],
    *,
    update_existing: bool = False,
) -> int:
    """Append deduplicated records to an XLSX workbook using optional openpyxl."""
    openpyxl = _openpyxl()
    target = Path(path)
    existing: list[dict[str, Any]] = []
    if target.exists():
        workbook = openpyxl.load_workbook(target)
        sheet = workbook.active
        values = list(sheet.values)
        if values:
            headers = [str(value) if value is not None else "" for value in values[0]]
            existing = [dict(zip(headers, row)) for row in values[1:]]
    else:
        workbook = openpyxl.Workbook()
        sheet = workbook.active

    rows, headers, changed = _merge(existing, records, update_existing)
    if sheet.max_row:
        sheet.delete_rows(1, sheet.max_row)
    if headers:
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(field, "") for field in headers])
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(target)
    return len(changed)


def sync_google_sheet(
    records: Iterable[Mapping[str, Any]],
    *,
    worksheet: Worksheet | None = None,
    client: Any = None,
    spreadsheet: str | None = None,
    worksheet_name: str | None = None,
    update_existing: bool = False,
) -> int:
    """Sync through an injected authenticated worksheet or gspread-like client."""
    if worksheet is None:
        if client is None or spreadsheet is None:
            raise ValueError("provide worksheet, or authenticated client and spreadsheet")
        book = client.open(spreadsheet)
        worksheet = book.worksheet(worksheet_name) if worksheet_name else book.sheet1

    existing = worksheet.get_all_records()
    rows, headers, changed = _merge(existing, records, update_existing)
    if not changed:
        return 0

    existing_count = len(existing)
    header_changed = any(field not in (list(existing[0]) if existing else []) for field in headers)
    updates_existing = any(index < existing_count for index in changed)
    if update_existing and (updates_existing or header_changed):
        worksheet.update("A1", [headers] + [[row.get(field, "") for field in headers] for row in rows])
    else:
        if not existing:
            worksheet.append_rows([headers] + [[row.get(field, "") for field in headers] for row in rows])
        else:
            new_rows = [rows[index] for index in changed if index >= existing_count]
            if new_rows:
                worksheet.append_rows([[row.get(field, "") for field in headers] for row in new_rows])
    return len(changed)
