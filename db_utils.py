"""SQLite connections that close deterministically after ``with`` blocks."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(database: str | Path, *args: Any, **kwargs: Any) -> sqlite3.Connection:
    kwargs.setdefault("factory", ClosingConnection)
    return sqlite3.connect(database, *args, **kwargs)
