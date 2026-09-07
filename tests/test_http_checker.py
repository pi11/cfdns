from datetime import UTC, datetime

import httpx
import pytest

from app.http_checker import HTTPCheck, inspect_url, normalize_http_target
from app.models import DNSRecord, HTTPCheckResult


@pytest.mark.parametrize(
    ("scheme", "page", "expected"),
    [
        ("https", "/health", ("https", "/health")),
        ("http", "status?full=1", ("http", "/status?full=1")),
        ("http", "https://example.com/health?deep=1", ("https", "/health?deep=1")),
    ],
)
def test_normalize_http_target(
    scheme: str, page: str, expected: tuple[str, str]
) -> None:
    assert normalize_http_target("example.com", scheme, page) == expected


def test_normalize_http_target_rejects_another_host() -> None:
    with pytest.raises(ValueError, match="must match"):
        normalize_http_target("example.com", "https", "https://other.example/health")


@pytest.mark.asyncio
async def test_inspect_url_records_success_and_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(302, headers={"Location": "/ready"})
        return httpx.Response(204)

    check = await inspect_url(
        "https://example.com/health", transport=httpx.MockTransport(handler)
    )

    assert check.status == "healthy"
    assert check.status_code == 204
    assert check.final_url == "https://example.com/ready"
    assert check.latency_ms is not None


@pytest.mark.asyncio
async def test_inspect_url_records_http_error() -> None:
    check = await inspect_url(
        "https://example.com/health",
        transport=httpx.MockTransport(lambda _request: httpx.Response(503)),
    )

    assert check.status == "http_error"
    assert check.status_code == 503
    assert check.error == "Server returned HTTP 503."


def test_record_http_display_status() -> None:
    record = DNSRecord(
        zone_id=1,
        cloudflare_id="record-id",
        record_type="A",
        name="example.com",
        content="192.0.2.1",
        http_check_scheme="https",
        http_check_path="/health",
    )
    assert record.http_check_url == "https://example.com/health"
    assert record.http_display_status == "pending"

    record.http_result = HTTPCheckResult(
        url=record.http_check_url,
        status="healthy",
        status_code=200,
        checked_at=datetime.now(UTC),
    )
    assert record.http_display_status == "ok"

    record.http_result.status = "timeout"
    assert record.http_display_status == "danger"


@pytest.mark.asyncio
async def test_inspect_url_reports_an_unusable_hostname() -> None:
    # Cloudflare accepts record names that httpx cannot turn into a URL.
    check = await inspect_url("https://xn--a.example.com/")

    assert check.status == "invalid_url"
    assert check.error


@pytest.mark.asyncio
async def test_check_all_enabled_records_survives_one_broken_record(monkeypatch) -> None:
    from app import http_checker

    async def explode(_record: DNSRecord) -> None:
        raise RuntimeError("unexpected failure")

    records = [
        DNSRecord(
            id=index,
            zone_id=1,
            cloudflare_id=f"record-{index}",
            record_type="A",
            name=f"host{index}.example.com",
            content="192.0.2.1",
            http_check_enabled=True,
        )
        for index in (1, 2)
    ]
    stored: list[int] = []

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def scalars(self, _statement):
            return records

        async def rollback(self) -> None:
            return None

    async def inspect_record(record: DNSRecord) -> HTTPCheck:
        if record.id == 1:
            await explode(record)
        return HTTPCheck(record.http_check_url, None, "healthy", 200, 1.0)

    async def store_record_result(_session, record, _check, **_kwargs) -> None:
        stored.append(record.id)

    monkeypatch.setattr(http_checker, "SessionLocal", FakeSession)
    monkeypatch.setattr(http_checker, "inspect_record", inspect_record)
    monkeypatch.setattr(http_checker, "store_record_result", store_record_result)

    checked, failed = await http_checker.check_all_enabled_records()

    assert stored == [2]
    assert (checked, failed) == (1, 1)
