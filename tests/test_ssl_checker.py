from datetime import UTC, datetime, timedelta

import pytest

from app.models import DNSRecord, SSLCheckResult
from app.ssl_checker import resolve_record_addresses


@pytest.mark.asyncio
async def test_a_and_aaaa_records_use_their_stored_address() -> None:
    assert await resolve_record_addresses("A", "192.0.2.10") == ["192.0.2.10"]
    assert await resolve_record_addresses("AAAA", "2001:0db8::1") == ["2001:db8::1"]


def test_record_uses_earliest_ssl_expiry() -> None:
    later = datetime.now(UTC) + timedelta(days=90)
    earlier = datetime.now(UTC) + timedelta(days=20)
    record = DNSRecord(
        zone_id=1,
        cloudflare_id="record-id",
        record_type="A",
        name="example.com",
        content="192.0.2.10",
    )
    record.ssl_results = [
        SSLCheckResult(ip_address="192.0.2.10", status="valid", certificate_expires_at=later),
        SSLCheckResult(ip_address="192.0.2.11", status="valid", certificate_expires_at=earlier),
    ]

    assert record.ssl_earliest_expiry == earlier
    assert record.ssl_display_status == "notice"


def test_any_failed_endpoint_marks_record_as_danger() -> None:
    record = DNSRecord(
        zone_id=1,
        cloudflare_id="record-id",
        record_type="CNAME",
        name="example.com",
        content="target.example.net",
    )
    record.ssl_results = [
        SSLCheckResult(ip_address="192.0.2.10", status="connection_error", error="Timeout")
    ]

    assert record.ssl_display_status == "danger"
