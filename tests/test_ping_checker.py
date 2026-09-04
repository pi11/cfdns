from datetime import UTC, datetime

from app.models import DNSRecord, PingCheckResult


def test_record_ping_status_uses_all_resolved_addresses() -> None:
    record = DNSRecord(
        zone_id=1,
        cloudflare_id="record-id",
        record_type="A",
        name="example.com",
        content="192.0.2.1",
        ping_check_enabled=True,
    )
    record.ping_results = [
        PingCheckResult(
            ip_address="192.0.2.1",
            status="reachable",
            latency_ms=12.5,
            checked_at=datetime.now(UTC),
        )
    ]
    assert record.ping_display_status == "ok"

    record.ping_results.append(
        PingCheckResult(
            ip_address="192.0.2.2",
            status="unreachable",
            checked_at=datetime.now(UTC),
        )
    )
    assert record.ping_display_status == "danger"
