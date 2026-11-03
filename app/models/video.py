from dataclasses import dataclass
import sqlite3
from collections.abc import Mapping


@dataclass(frozen=True)
class Video:
    id: str
    project_id: str
    sequence_number: int
    video_code: str
    title: str
    source_url: str
    normalized_url: str
    download_status: str
    local_video_path: str | None
    thumbnail_path: str | None
    facebook_uploaded: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(
        cls, row: Mapping[str, object] | sqlite3.Row
    ) -> "Video":
        return cls(
            **{
                key: bool(row[key]) if key == "facebook_uploaded" else row[key]
                for key in cls.__dataclass_fields__
            }
        )
