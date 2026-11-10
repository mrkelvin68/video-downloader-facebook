import csv
from io import StringIO

from app.core.exceptions import CsvValidationError
from app.core.validators import normalize_video_url, validate_supported_video_url


def _dialect(text: str) -> type[csv.Dialect]:
    try:
        return csv.Sniffer().sniff(text[:8192], delimiters=",;")
    except csv.Error:
        first_line = text.splitlines()[0] if text.splitlines() else ""
        delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
        return type(
            "DetectedDialect",
            (csv.excel,),
            {"delimiter": delimiter},
        )


def parse_video_csv(text: str) -> tuple[list[dict[str, object]], list[str]]:
    payload = text.lstrip("\ufeff")
    if not payload.strip():
        return [], ["CSV rỗng"]
    reader = csv.reader(
        StringIO(payload, newline=""), dialect=_dialect(payload), strict=True
    )
    try:
        headers = next(reader, None)
    except csv.Error as exc:
        return [], [f"Dòng {reader.line_num or 1}: CSV không hợp lệ: {exc}"]
    if headers is None:
        return [], ["CSV rỗng"]
    normalized_headers = [header.strip().lower() for header in headers]
    aliases = {"sequence": "stt", "title": "ten_video"}
    normalized_headers = [aliases.get(header, header) for header in normalized_headers]
    missing = [column for column in ("ten_video", "url") if column not in normalized_headers]
    if missing:
        return [], [f"CSV thiếu cột: {', '.join(missing)}"]
    if len(set(normalized_headers)) != len(normalized_headers):
        return [], ["CSV có tên cột trùng"]
    if len(headers) not in (2, 3):
        return [], ["CSV cần đúng 2 cột: title,url"]
    positions = {header: index for index, header in enumerate(normalized_headers)}
    has_legacy_sequence = "stt" in positions
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    urls: dict[str, tuple[int, str]] = {}
    data_count = 0
    previous_line_number = reader.line_num
    while True:
        line_number = previous_line_number + 1
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            errors.append(f"Dòng {line_number}: CSV không hợp lệ: {exc}")
            break
        previous_line_number = reader.line_num
        if not row or all(not value.strip() for value in row):
            continue
        data_count += 1
        if len(row) > len(headers):
            errors.append(f"Dòng {line_number}: thừa cột")
            continue
        if len(row) < len(headers):
            errors.append(f"Dòng {line_number}: thiếu cột")
            continue
        title = row[positions["ten_video"]].strip()
        url = row[positions["url"]].strip()
        row_errors: list[str] = []
        sequence = len(rows) + 1
        if has_legacy_sequence:
            raw_sequence = row[positions["stt"]].strip()
            if not raw_sequence.isdecimal():
                row_errors.append("stt phải là số nguyên dương")
            else:
                try:
                    sequence = int(raw_sequence)
                except ValueError:
                    row_errors.append("stt vượt giới hạn số nguyên")
                if sequence < 1:
                    row_errors.append("stt phải là số nguyên dương")
                elif sequence > 2**63 - 1:
                    row_errors.append("stt vượt giới hạn SQLite")
        if not title:
            row_errors.append("tên video không được để trống")
        valid_url = validate_supported_video_url(url)
        if not valid_url:
            host = url.split("//", 1)[-1].split("/", 1)[0].lower()
            platform = (
                "YouTube" if "youtu" in host else "Instagram"
                if "instagram" in host else "Facebook"
            )
            row_errors.append(f"URL {platform} không hợp lệ")
        normalized_url = normalize_video_url(url) if valid_url else ""
        if valid_url and normalized_url in urls:
            first_input_row, _ = urls[normalized_url]
            input_row = data_count
            row_errors.append(
                "Lỗi import video\n"
                f"Dòng nhập: {input_row}\n"
                f"URL: {url}\n"
                f"ID dòng nhập: {input_row}\n"
                f"Trùng với dòng nhập: {first_input_row}\n"
                "Nguyên nhân: URL đã xuất hiện trong danh sách nhập."
            )
        if row_errors:
            errors.append(f"Dòng {line_number}: {'; '.join(row_errors)}")
            continue
        urls[normalized_url] = (data_count, url)
        rows.append(
            {
                "stt": sequence,
                "ten_video": title,
                "url": url,
            }
        )
    if data_count == 0 and not errors:
        return [], ["CSV không có dữ liệu"]
    if errors:
        return [], errors
    return rows, []


def parse_and_validate_video_csv(text: str) -> list[dict[str, object]]:
    rows, errors = parse_video_csv(text)
    if errors:
        raise CsvValidationError(errors)
    return rows


def export_video_csv(rows: list[object]) -> str:
    output = StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(("title", "url"))
    writer.writerows(rows)
    return output.getvalue()


def sample_video_csv() -> str:
    return (
        "title,url\n"
        "Video mẫu,https://www.facebook.com/watch/?v=example\n"
    )
