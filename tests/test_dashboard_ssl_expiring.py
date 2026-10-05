from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.requests import Request

from app.main import app
from app.models import Account, DNSRecord, SSLCheckResult, Zone
from app.routes import dashboard


@pytest.mark.asyncio
async def test_expiring_ssl_tile_counts_records_and_filters_results(session) -> None:
    account = Account(name="Production", encrypted_token="test")
    zone = Zone(account=account, cloudflare_id="zone", name="example.com")
    session.add_all([account, zone])
    await session.flush()

    now = datetime.now(UTC)

    def add_record(name: str, expiries: list[tuple[int, str]], *, enabled: bool = True) -> None:
        record = DNSRecord(
            zone=zone,
            cloudflare_id=name,
            record_type="A",
            name=f"{name}.example.com",
            content="192.0.2.1",
            ssl_check_enabled=enabled,
        )
        record.ssl_results = [
            SSLCheckResult(
                ip_address=f"192.0.2.{index + 1}",
                status=status,
                certificate_expires_at=now + timedelta(days=days),
            )
            for index, (days, status) in enumerate(expiries)
        ]
        session.add(record)

    add_record("soon", [(7, "valid"), (10, "valid")])
    add_record("boundary", [(14, "valid")])
    add_record("later", [(15, "valid")])
    add_record("expired", [(-1, "expired")])
    add_record("invalid", [(7, "hostname_mismatch")])
    add_record("disabled", [(7, "valid")], enabled=False)
    await session.commit()

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [],
            "app": app,
        }
    )

    def render(status: str = ""):
        return dashboard(
            request,
            q="",
            account_id="",
            zone_id="",
            record_type="",
            proxy_status="",
            ssl_status=status,
            page=1,
            per_page="25",
            session=session,
        )

    response = await render()
    assert response.context["ssl_expiring_count"] == 2
    assert response.context["total_records"] == 6
    assert "SSL expiring ≤14 days" in response.body.decode()
    assert "<span>Accounts</span>" not in response.body.decode()
    assert parse_qs(urlparse(response.context["ssl_expiring_filter_url"]).query)["ssl_status"] == [
        "expiring"
    ]

    filtered = await render("expiring")
    assert filtered.context["total_records"] == 2
    assert {record.name for record in filtered.context["records"]} == {
        "soon.example.com",
        "boundary.example.com",
    }
    assert parse_qs(urlparse(filtered.context["ssl_expiring_filter_url"]).query).get(
        "ssl_status"
    ) is None
