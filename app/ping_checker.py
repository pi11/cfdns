from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import SessionLocal
from app.models import DNSRecord, PingCheckResult
from app.ssl_checker import ELIGIBLE_RECORD_TYPES, resolve_record_addresses

PING_COUNT = 3
PING_TIMEOUT_SECONDS = 8
PING_CONCURRENCY = 20
_RTT_PATTERN = re.compile(r"(?:rtt|round-trip).*?=\s*[\d.]+/([\d.]+)/")


@dataclass(slots=True)
class PingCheck:
    ip_address: str
    status: str
    latency_ms: float | None = None
    error: str | None = None


async def ping_endpoint(ip_address: str) -> PingCheck:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        process = await asyncio.create_subprocess_exec(
            "ping",
            "-n",
            "-c",
            str(PING_COUNT),
            "-W",
            "2",
            ip_address,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        return PingCheck(ip_address, "error", error=str(exc))
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), PING_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return PingCheck(ip_address, "unreachable", error="Ping timed out.")
    output = stdout.decode(errors="replace")
    if process.returncode == 0:
        match = _RTT_PATTERN.search(output)
        latency = float(match.group(1)) if match else None
        return PingCheck(ip_address, "reachable", latency)
    output_lines = output.strip().splitlines()
    detail = stderr.decode(errors="replace").strip() or (
        output_lines[-1] if output_lines else f"ping exited with status {process.returncode}"
    )
    return PingCheck(ip_address, "unreachable", error=detail[:1000])


async def inspect_record(
    record: DNSRecord, semaphore: asyncio.Semaphore | None = None
) -> list[PingCheck]:
    if record.record_type not in ELIGIBLE_RECORD_TYPES:
        return []
    try:
        addresses = await resolve_record_addresses(record.record_type, record.content)
    except (OSError, ValueError) as exc:
        return [PingCheck("unresolved", "resolution_error", error=str(exc))]
    if not addresses:
        return [PingCheck("unresolved", "resolution_error", error="No IP addresses found.")]

    limit = semaphore or asyncio.Semaphore(PING_CONCURRENCY)

    async def limited_ping(address: str) -> PingCheck:
        async with limit:
            return await ping_endpoint(address)

    return list(await asyncio.gather(*(limited_ping(address) for address in addresses)))


async def check_and_store_record(
    session: AsyncSession, record: DNSRecord, semaphore: asyncio.Semaphore | None = None
) -> list[PingCheck]:
    checks = await inspect_record(record, semaphore)
    await store_record_results(session, record, checks)
    return checks


async def store_record_results(
    session: AsyncSession,
    record: DNSRecord,
    checks: list[PingCheck],
    *,
    notify: bool = True,
) -> None:
    await session.execute(delete(PingCheckResult).where(PingCheckResult.record_id == record.id))
    checked_at = datetime.now(UTC)
    session.add_all(
        PingCheckResult(
            record_id=record.id,
            ip_address=check.ip_address,
            status=check.status,
            latency_ms=check.latency_ms,
            error=check.error,
            checked_at=checked_at,
        )
        for check in checks
    )
    await session.commit()
    if notify:
        from app.config import get_settings
        from app.notifications import notify_ping_results

        await notify_ping_results(session, record, checks, get_settings())


async def check_all_enabled_records() -> tuple[int, int]:
    async with SessionLocal() as session:
        records = list(
            await session.scalars(
                select(DNSRecord)
                .options(joinedload(DNSRecord.zone))
                .where(
                    DNSRecord.ping_check_enabled.is_(True),
                    DNSRecord.record_type.in_(ELIGIBLE_RECORD_TYPES),
                )
            )
        )
        semaphore = asyncio.Semaphore(PING_CONCURRENCY)
        inspected = await asyncio.gather(
            *(inspect_record(record, semaphore) for record in records), return_exceptions=True
        )
        checked = failed = 0
        for record, results in zip(records, inspected, strict=True):
            try:
                if isinstance(results, BaseException):
                    raise results
                await store_record_results(session, record, results)
                checked += 1
                failed += any(result.status != "reachable" for result in results)
            except Exception:
                await session.rollback()
                failed += 1
        return checked, failed
