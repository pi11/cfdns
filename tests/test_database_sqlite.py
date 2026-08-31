import os

import pytest
from sqlalchemy import text

from app.database import engine, is_sqlite


@pytest.mark.skipif(
    os.environ.get("RUN_SQLITE_INTEGRATION") != "1",
    reason="Run against a file-backed SQLite database in CI or the container smoke test.",
)
@pytest.mark.asyncio
async def test_sqlite_safety_pragmas_are_enabled() -> None:
    assert is_sqlite
    async with engine.connect() as connection:
        foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
        journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
        busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))

    assert foreign_keys == 1
    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == 30000
