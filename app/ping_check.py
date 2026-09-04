import asyncio

from app.ping_checker import check_all_enabled_records


async def main() -> None:
    checked, failed = await check_all_enabled_records()
    print(f"Ping checks completed: {checked} records checked, {failed} records with errors.")


if __name__ == "__main__":
    asyncio.run(main())
