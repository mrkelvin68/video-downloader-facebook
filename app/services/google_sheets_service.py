from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

from app.core.validators import normalize_video_url
from app.services.csv_service import parse_video_csv


_SHEET_ID = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def _spreadsheet_id(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc not in {"docs.google.com", "docs.googleusercontent.com"}:
        raise ValueError("Google Sheets URL không hợp lệ.")
    match = _SHEET_ID.search(parsed.path)
    if not match:
        raise ValueError("Google Sheets URL không chứa Spreadsheet ID hợp lệ.")
    return match.group(1)


def google_sheet_csv_url(url: str, sheet_name: str = "") -> str:
    spreadsheet_id = _spreadsheet_id(url)
    query = parse_qs(urlsplit(url).query)
    gid = str(query.get("gid", [""])[0]).strip()
    if sheet_name.strip():
        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={quote(sheet_name.strip())}"
    suffix = f"&gid={quote(gid)}" if gid else ""
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv{suffix}"


def fetch_google_sheet_csv(url: str, sheet_name: str = "", timeout: float = 20.0) -> str:
    export_url = google_sheet_csv_url(url, sheet_name)
    request = Request(export_url, headers={"User-Agent": "VideoDownloader/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("Google Sheets không có quyền đọc. Hãy chia sẻ công khai hoặc cấu hình Google OAuth phù hợp.") from exc
        if exc.code == 404:
            raise ValueError("Không tìm thấy Spreadsheet hoặc Sheet/Tab.") from exc
        if exc.code == 429:
            raise ValueError("Google Sheets API quota đã vượt giới hạn, hãy thử lại sau.") from exc
        raise ValueError(f"Google Sheets trả về HTTP {exc.code}.") from exc
    except (TimeoutError, URLError) as exc:
        raise ValueError(f"Không thể đọc Google Sheets: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("Google Sheets không trả về dữ liệu UTF-8 hợp lệ.") from exc


def rows_to_video_csv(rows: list[Mapping[str, object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("stt", "title", "url"))
    writer.writerows((row["stt"], row["ten_video"], row["url"]) for row in rows)
    return output.getvalue()


def parse_google_sheet_csv(text: str) -> tuple[str, list[dict[str, object]], list[str], dict[str, int]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline=""))
    headers = [str(header or "").strip().lower() for header in (reader.fieldnames or [])]
    aliases = {"title": "ten_video", "name": "ten_video", "video_url": "url"}
    headers = [aliases.get(header, header) for header in headers]
    if "url" not in headers:
        return "", [], ["Google Sheets thiếu cột bắt buộc: url"], {"total": 0, "valid": 0, "invalid": 0, "duplicates": 0}
    if len(set(headers)) != len(headers):
        return "", [], ["Google Sheets có tên cột trùng"], {"total": 0, "valid": 0, "invalid": 0, "duplicates": 0}

    positions = {header: index for index, header in enumerate(headers)}
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    seen_urls: set[str] = set()
    total = duplicates = 0
    for line, raw in enumerate(reader, 2):
        if not raw or all(not str(value or "").strip() for value in raw.values()):
            continue
        total += 1
        values = list(raw.values())
        normalized_row = {header: str(values[index] or "") for index, header in enumerate(headers)}
        stt = total
        title = normalized_row.get("ten_video", "").strip() or f"Video {total}"
        url = normalized_row.get("url", "").strip()
        normalized = normalize_video_url(url) if url else ""
        if normalized in seen_urls:
            errors.append(f"Dòng {line}: URL trùng")
            duplicates += 1
            continue
        candidate = rows_to_video_csv([{"stt": stt, "ten_video": title, "url": url}])
        parsed_rows, row_errors = parse_video_csv(candidate)
        if row_errors:
            errors.extend(f"Dòng {line}: {error.split(': ', 1)[-1]}" for error in row_errors)
            continue
        seen_urls.add(normalized)
        rows.extend(parsed_rows)

    stats = {"total": total, "valid": len(rows), "invalid": total - len(rows), "duplicates": duplicates}
    if not total:
        errors.append("Google Sheets không có dữ liệu")
    return rows_to_video_csv(rows) if rows and not errors else "", rows, errors, stats
