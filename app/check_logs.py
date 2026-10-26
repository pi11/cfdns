from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CheckLog, DNSRecord

if TYPE_CHECKING:
    from app.http_checker import HTTPCheck
    from app.ping_checker import PingCheck
    from app.ssl_checker import EndpointCheck

CHECK_TYPES = {"ssl": "SSL", "ping": "Ping", "http": "GET"}


def ssl_log_entries(
    record: DNSRecord, checks: list[EndpointCheck], checked_at: datetime
) -> list[CheckLog]:
    return [
        CheckLog(
            check_type="ssl",
            record_id=record.id,
            record_name=record.name,
            target=check.ip_address,
            status=check.status,
            success=check.status == "valid",
            certificate_expires_at=check.certificate_expires_at,
            error=check.error,
            checked_at=checked_at,
        )
        for check in checks
    ]


def ping_log_entries(
    record: DNSRecord, checks: list[PingCheck], checked_at: datetime
) -> list[CheckLog]:
    return [
        CheckLog(
            check_type="ping",
            record_id=record.id,
            record_name=record.name,
            target=check.ip_address,
            status=check.status,
            success=check.status == "reachable",
            latency_ms=check.latency_ms,
            error=check.error,
            checked_at=checked_at,
        )
        for check in checks
    ]


def http_log_entry(record: DNSRecord, check: HTTPCheck, checked_at: datetime) -> CheckLog:
    return CheckLog(
        check_type="http",
        record_id=record.id,
        record_name=record.name,
        target=check.url,
        status=check.status,
        success=check.status == "healthy",
        status_code=check.status_code,
        latency_ms=check.latency_ms,
        error=check.error,
        checked_at=checked_at,
    )


async def prune_check_logs(session: AsyncSession, retention_days: int) -> None:
    if retention_days <= 0:
        return
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    await session.execute(delete(CheckLog).where(CheckLog.checked_at < cutoff))
    await session.commit()
