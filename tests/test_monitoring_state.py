import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from starlette.requests import Request

from app import notifications, routes, ssl_checker
from app.config import get_settings
from app.models import (
    AppSettings,
    DNSRecord,
    SSLCheckResult,
    SSLNotificationState,
    Zone,
)
from app.security import TokenCipher
from app.ssl_checker import EndpointCheck


class RecordingTelegramClient:
    sent: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self) -> "RecordingTelegramClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def send_message(self, _chat_id: str, text: str) -> None:
        RecordingTelegramClient.sent.append(text)


def _plain_request() -> Request:
    return Request({"type": "http", "method": "POST", "headers": [], "query_string": b""})


async def _record_with_failed_ssl_check(session) -> DNSRecord:
    zone = Zone(account_id=1, cloudflare_id="zone-id", name="example.com")
    session.add(zone)
    await session.flush()
    record = DNSRecord(
        zone_id=zone.id,
        cloudflare_id="record-id",
        record_type="A",
        name="a.example.com",
        content="192.0.2.1",
        ssl_check_enabled=True,
    )
    session.add(record)
    session.add(
        AppSettings(
            id=1,
            encrypted_telegram_token=TokenCipher(get_settings().encryption_key).encrypt("bot"),
            telegram_chat_id="42",
        )
    )
    await session.commit()
    # Notifications read record.zone, which every caller eager-loads.
    return await session.scalar(
        select(DNSRecord).options(joinedload(DNSRecord.zone)).where(DNSRecord.id == record.id)
    )


@pytest.mark.asyncio
async def test_disabling_ssl_checks_clears_notification_state(session, monkeypatch) -> None:
    monkeypatch.setattr(notifications, "TelegramClient", RecordingTelegramClient)
    RecordingTelegramClient.sent = []
    record = await _record_with_failed_ssl_check(session)
    failure = EndpointCheck("192.0.2.1", None, "connection_error", "Connection refused")

    await notifications.notify_ssl_results(session, record, [failure], get_settings())
    session.add(
        SSLCheckResult(record_id=record.id, ip_address="192.0.2.1", status="connection_error")
    )
    await session.commit()
    assert len(RecordingTelegramClient.sent) == 1

    await routes.toggle_ssl_check(_plain_request(), record.id, enabled=False, session=session)

    assert list(await session.scalars(select(SSLCheckResult))) == []
    assert list(await session.scalars(select(SSLNotificationState))) == []

    # Re-enabling while the endpoint is still broken must alert again.
    await notifications.notify_ssl_results(session, record, [failure], get_settings())
    assert len(RecordingTelegramClient.sent) == 2


@pytest.mark.asyncio
async def test_rechecking_prunes_state_for_addresses_that_disappeared(
    session, monkeypatch
) -> None:
    monkeypatch.setattr(notifications, "TelegramClient", RecordingTelegramClient)
    RecordingTelegramClient.sent = []
    record = await _record_with_failed_ssl_check(session)

    failures = [
        EndpointCheck("192.0.2.1", None, "connection_error", "refused"),
        EndpointCheck("192.0.2.2", None, "connection_error", "refused"),
    ]
    await notifications.notify_ssl_results(session, record, failures, get_settings())
    assert len(RecordingTelegramClient.sent) == 2

    # The record now resolves to a single address; the other one must not linger.
    monkeypatch.setattr(ssl_checker, "inspect_record", lambda _record: _returns(failures[:1]))
    await ssl_checker.check_and_store_record(session, record)

    remaining = list(await session.scalars(select(SSLNotificationState)))
    assert [state.ip_address for state in remaining] == ["192.0.2.1"]


async def _returns(value):
    return value
