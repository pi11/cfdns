import httpx
import pytest

from app import routes
from app.auth import COOKIE_NAME, session_token
from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models import Account, DNSRecord, Zone
from app.security import TokenCipher


@pytest.mark.asyncio
async def test_direct_get_option_is_saved_without_cloudflare_proxy(session, monkeypatch) -> None:
    settings = get_settings()
    account = Account(
        name="Main",
        encrypted_token=TokenCipher(settings.encryption_key).encrypt("token"),
    )
    session.add(account)
    await session.flush()
    zone = Zone(account_id=account.id, cloudflare_id="zone-id", name="example.com")
    session.add(zone)
    await session.flush()
    records = [
        DNSRecord(
            zone_id=zone.id,
            cloudflare_id=f"record-{index}",
            record_type="A",
            name=f"host{index}.example.com",
            content=f"192.0.2.{index}",
            proxied=False,
        )
        for index in (1, 2)
    ]
    session.add_all(records)
    await session.commit()

    class FakeCloudflareClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def update_record(self, _zone_id, record_id, payload):
            return {"id": record_id, **payload}

    checked: list[bool] = []

    async def fake_check(_session, _record, *, direct=False):
        checked.append(direct)

    async def no_proxy(_session, _settings):
        return None

    monkeypatch.setattr(routes, "CloudflareClient", FakeCloudflareClient)
    monkeypatch.setattr(routes, "global_proxy", no_proxy)
    monkeypatch.setattr(routes.http_checker, "check_and_store_record", fake_check)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    cookies = {COOKIE_NAME: session_token(settings.admin_password, settings.encryption_key)}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", cookies=cookies
        ) as client:
            response = await client.post(
                f"/records/{records[0].id}",
                data={
                    "type": "A",
                    "name": records[0].name,
                    "content": records[0].content,
                    "http_check_enabled": "true",
                    "http_check_direct_enabled": "true",
                },
            )
            edit_response = await client.get(f"/records/{records[0].id}/edit")
            dashboard_response = await client.get("/")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert records[0].http_check_enabled is True
    assert records[0].proxied is False
    assert records[0].http_check_direct_enabled is True
    assert records[1].http_check_direct_enabled is False
    assert checked == [False, True]
    assert 'name="http_check_direct_enabled"' in edit_response.text
    assert "GET direct pending" in dashboard_response.text
