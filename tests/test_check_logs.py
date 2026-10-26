from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import http_checker, ping_checker
from app.auth import COOKIE_NAME, session_token
from app.check_logs import prune_check_logs
from app.config import get_settings
from app.database import get_db
from app.http_checker import HTTPCheck
from app.main import app
from app.models import CheckLog, DNSRecord, PingCheckResult, Zone
from app.ping_checker import PingCheck


async def _record(session) -> DNSRecord:
    zone = Zone(account_id=1, cloudflare_id="zone-id", name="example.com")
    session.add(zone)
    await session.flush()
    record = DNSRecord(
        zone_id=zone.id,
        cloudflare_id="record-id",
        record_type="A",
        name="a.example.com",
        content="192.0.2.1",
    )
    session.add(record)
    await session.commit()
    return record


@pytest.mark.asyncio
async def test_every_check_run_is_appended_to_the_log(session) -> None:
    record = await _record(session)

    await ping_checker.store_record_results(
        session, record, [PingCheck("192.0.2.1", "reachable", 12.5)], notify=False
    )
    await ping_checker.store_record_results(
        session, record, [PingCheck("192.0.2.1", "unreachable", error="timeout")], notify=False
    )
    await http_checker.store_record_result(
        session,
        record,
        HTTPCheck("https://a.example.com/", "https://a.example.com/", "http_error", 503),
        notify=False,
    )

    # The latest-result table still keeps one row; the log keeps the history.
    assert len(list(await session.scalars(select(PingCheckResult)))) == 1
    logs = list(await session.scalars(select(CheckLog).order_by(CheckLog.id)))
    assert [(log.check_type, log.status, log.success) for log in logs] == [
        ("ping", "reachable", True),
        ("ping", "unreachable", False),
        ("http", "http_error", False),
    ]
    assert logs[0].latency_ms == 12.5
    assert logs[2].status_code == 503
    assert all(log.record_name == "a.example.com" for log in logs)


@pytest.mark.asyncio
async def test_prune_removes_only_entries_past_retention(session) -> None:
    now = datetime.now(UTC)
    for age_days in (1, 45):
        session.add(
            CheckLog(
                check_type="ping",
                record_name="a.example.com",
                target="192.0.2.1",
                status="reachable",
                success=True,
                checked_at=now - timedelta(days=age_days),
            )
        )
    await session.commit()

    await prune_check_logs(session, 0)
    assert len(list(await session.scalars(select(CheckLog)))) == 2

    await prune_check_logs(session, 30)
    remaining = list(await session.scalars(select(CheckLog)))
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_logs_page_lists_and_filters_results(session) -> None:
    record = await _record(session)
    await ping_checker.store_record_results(
        session, record, [PingCheck("192.0.2.1", "reachable", 3.0)], notify=False
    )
    await http_checker.store_record_result(
        session,
        record,
        HTTPCheck("https://a.example.com/", None, "connection_error", error="refused"),
        notify=False,
    )

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    settings = get_settings()
    cookies = {COOKIE_NAME: session_token(settings.admin_password, settings.encryption_key)}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", cookies=cookies
        ) as client:
            response = await client.get("/logs")
            failed = await client.get("/logs", params={"result": "failed"})
            by_record = await client.get("/logs", params={"record_id": record.id + 1})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "192.0.2.1" in response.text and "refused" in response.text
    assert "refused" in failed.text and "192.0.2.1" not in failed.text
    assert "No check results logged" in by_record.text
