from urllib.parse import parse_qs, urlparse

import pytest
from starlette.requests import Request

from app.main import app
from app.models import Account, DNSRecord, HTTPCheckResult, HTTPDirectCheckResult, Zone
from app.routes import dashboard


@pytest.mark.asyncio
async def test_get_errors_tile_counts_records_and_filters_both_check_types(session) -> None:
    account = Account(name="Production", encrypted_token="test")
    zone = Zone(account=account, cloudflare_id="zone", name="example.com")
    session.add(zone)
    await session.flush()

    def add_record(name: str, regular: str | None, direct: str | None) -> None:
        record = DNSRecord(
            zone=zone,
            cloudflare_id=name,
            record_type="A",
            name=f"{name}.example.com",
            content="192.0.2.1",
            http_check_enabled=True,
            http_check_direct_enabled=direct is not None,
        )
        if regular:
            record.http_result = HTTPCheckResult(url=f"https://{record.name}/", status=regular)
        if direct:
            record.http_direct_result = HTTPDirectCheckResult(
                url=f"https://{record.name}/", origin="192.0.2.1", status=direct
            )
        session.add(record)

    add_record("regular", "http_error", None)
    add_record("direct", "healthy", "timeout")
    add_record("both", "connection_error", "http_error")
    add_record("healthy", "healthy", "healthy")
    add_record("pending", None, None)
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

    async def render(status: str = ""):
        return await dashboard(
            request,
            q="",
            account_id="",
            zone_id="",
            record_type="",
            proxy_status="",
            ssl_status="",
            get_status=status,
            page=1,
            per_page="25",
            session=session,
        )

    response = await render()
    assert response.context["get_error_count"] == 3
    assert response.context["total_records"] == 5
    assert "GET Errors" in response.body.decode()
    assert '<span>Zones</span>' not in response.body.decode()
    assert parse_qs(urlparse(response.context["get_error_filter_url"]).query)["get_status"] == [
        "error"
    ]

    filtered = await render("error")
    assert filtered.context["total_records"] == 3
    assert {record.name for record in filtered.context["records"]} == {
        "regular.example.com",
        "direct.example.com",
        "both.example.com",
    }
    assert "get_status" not in parse_qs(urlparse(filtered.context["get_error_filter_url"]).query)
