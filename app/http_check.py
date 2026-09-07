from __future__ import annotations

import asyncio

from app.http_checker import check_all_enabled_records

if __name__ == "__main__":
    checked, failed = asyncio.run(check_all_enabled_records())
    print(f"GET checks completed: {checked} records checked, {failed} records with errors.")
