from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import ssl_checker
from app.auth import COOKIE_NAME, session_token
from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models import Account, DNSRecord, SSLCheckResult, Zone
from app.ssl_checker import EndpointCheck


@pytest.mark.asyncio
async def test_recheck_ssl_returns_updated_row(session, monkeypatch) -> None:
    account = Account(name="Main", encrypted_token="token")
    session.add(account)
    await session.flush()
    zone = Zone(account_id=account.id, cloudflare_id="zone-id", name="example.com")
    session.add(zone)
    await session.flush()
    enabled = DNSRecord(
        zone_id=zone.id,
        cloudflare_id="record-1",
        record_type="A",
        name="a.example.com",
        content="192.0.2.1",
        ssl_check_enabled=True,
    )
    disabled = DNSRecord(
        zone_id=zone.id,
        cloudflare_id="record-2",
        record_type="A",
        name="b.example.com",
        content="192.0.2.2",
    )
    session.add_all([enabled, disabled])
    await session.commit()

    expires_at = datetime.now(UTC) + timedelta(days=60)

    async def fake_inspect(record, proxy_url=None):
        return [EndpointCheck("192.0.2.1", expires_at, "valid")]

    monkeypatch.setattr(ssl_checker, "inspect_record", fake_inspect)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    settings = get_settings()
    cookies = {COOKIE_NAME: session_token(settings.admin_password, settings.encryption_key)}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", cookies=cookies
        ) as client:
            response = await client.post(f"/records/{enabled.id}/ssl-check")
            rejected = await client.post(f"/records/{disabled.id}/ssl-check")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert f'id="record-{enabled.id}"' in response.text
    assert f"SSL until {expires_at:%Y-%m-%d}" in response.text
    assert rejected.status_code == 409
    stored = list(await session.scalars(select(SSLCheckResult)))
    assert [(row.record_id, row.status) for row in stored] == [(enabled.id, "valid")]
