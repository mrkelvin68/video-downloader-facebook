from dataclasses import dataclass
from pathlib import Path
import sqlite3
from collections.abc import Mapping


@dataclass(frozen=True)
class Project:
    uuid: str
    name: str
    prefix: str
    output_dir: str
    notes: str
    created_at: str
    updated_at: str
    matrix_count: int = 0

    @classmethod
    def from_row(
        cls, row: Mapping[str, object] | sqlite3.Row
    ) -> "Project":
        values = {
            key: str(row[key])
            for key in cls.__dataclass_fields__
            if key != "matrix_count"
        }
        try:
            values["matrix_count"] = int(row["matrix_count"])
        except (IndexError, KeyError):
            values["matrix_count"] = 0
        return cls(**values)

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)
