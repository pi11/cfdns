from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.check_logs import http_log_entry, prune_check_logs
from app.database import SessionLocal
from app.models import DNSRecord, HTTPCheckResult, HTTPDirectCheckResult
from app.ssl_checker import ELIGIBLE_RECORD_TYPES

HTTP_TIMEOUT_SECONDS = 15
HTTP_CONCURRENCY = 20
MAX_URL_LENGTH = 2400


@dataclass(slots=True)
class HTTPCheck:
    url: str
    final_url: str | None
    status: str
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None


class DirectOriginTransport(httpx.AsyncBaseTransport):
    """Connect to the record content while retaining the public Host and TLS identity."""

    def __init__(
        self, hostname: str, origin: str, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.hostname = hostname.rstrip(".").lower()
        self.origin = origin.rstrip(".")
        self.transport = transport or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host.rstrip(".").lower() != self.hostname:
            return await self.transport.handle_async_request(request)
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        extensions = {**request.extensions, "sni_hostname": self.hostname}
        origin_request = httpx.Request(
            request.method,
            request.url.copy_with(host=self.origin),
            headers=headers,
            content=await request.aread(),
            extensions=extensions,
        )
        return await self.transport.handle_async_request(origin_request)

    async def aclose(self) -> None:
        await self.transport.aclose()


def normalize_http_target(record_name: str, scheme: str, page: str) -> tuple[str, str]:
    scheme = scheme.strip().lower()
    if scheme not in {"http", "https"}:
        raise ValueError("GET check protocol must be http or https.")

    page = page.strip() or "/"
    if "://" in page:
        parsed = urlsplit(page)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("GET check page must be a valid HTTP or HTTPS URL.")
        if parsed.hostname.rstrip(".").lower() != record_name.rstrip(".").lower():
            raise ValueError("GET check URL host must match the DNS record name.")
        scheme = parsed.scheme
        page = parsed.path or "/"
        if parsed.query:
            page += f"?{parsed.query}"
    elif not page.startswith("/"):
        page = f"/{page}"
    if len(page) > 2048:
        raise ValueError("GET check page is too long.")
    return scheme, page


async def inspect_url(url: str, transport: httpx.AsyncBaseTransport | None = None) -> HTTPCheck:
    started = perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
            transport=transport,
            trust_env=False,
            headers={"User-Agent": "CFDNS-HTTP-Monitor/1.0"},
        ) as client:
            response = await client.get(url)
        latency_ms = (perf_counter() - started) * 1000
        final_url = str(response.url)[:MAX_URL_LENGTH]
        if response.is_success:
            return HTTPCheck(url, final_url, "healthy", response.status_code, latency_ms)
        return HTTPCheck(
            url,
            final_url,
            "http_error",
            response.status_code,
            latency_ms,
            f"Server returned HTTP {response.status_code}.",
        )
    except httpx.TimeoutException as exc:
        return HTTPCheck(url, None, "timeout", error=str(exc) or "Request timed out.")
    except httpx.HTTPError as exc:
        return HTTPCheck(url, None, "connection_error", error=str(exc))
    except (httpx.InvalidURL, ValueError) as exc:
        # Cloudflare accepts record names httpx cannot turn into a URL, such as a
        # malformed punycode label; UnicodeError from IDNA encoding is a ValueError.
        return HTTPCheck(url, None, "invalid_url", error=str(exc) or "Invalid URL.")


async def inspect_record(record: DNSRecord, *, direct: bool = False) -> HTTPCheck | None:
    if record.record_type not in ELIGIBLE_RECORD_TYPES:
        return None
    if direct:
        return await inspect_url(
            record.http_check_url,
            transport=DirectOriginTransport(record.name, record.content),
        )
    return await inspect_url(record.http_check_url)


async def store_record_result(
    session: AsyncSession, record: DNSRecord, check: HTTPCheck, *, notify: bool = True,
    direct: bool = False,
) -> None:
    result_model = HTTPDirectCheckResult if direct else HTTPCheckResult
    await session.execute(delete(result_model).where(result_model.record_id == record.id))
    checked_at = datetime.now(UTC)
    values = dict(
        record_id=record.id,
        url=check.url,
        final_url=check.final_url,
        status=check.status,
        status_code=check.status_code,
        latency_ms=check.latency_ms,
        error=check.error,
        checked_at=checked_at,
    )
    if direct:
        values["origin"] = record.content
    session.add(result_model(**values))
    session.add(http_log_entry(record, check, checked_at, direct=direct))
    await session.commit()
    if notify:
        from app.config import get_settings
        from app.notifications import notify_http_result

        await notify_http_result(session, record, check, get_settings(), direct=direct)


async def check_and_store_record(
    session: AsyncSession, record: DNSRecord, *, direct: bool = False
) -> HTTPCheck | None:
    check = await inspect_record(record, direct=True) if direct else await inspect_record(record)
    if check is not None:
        if direct:
            await store_record_result(session, record, check, direct=True)
        else:
            await store_record_result(session, record, check)
    return check


async def check_all_enabled_records() -> tuple[int, int]:
    async with SessionLocal() as session:
        records = list(
            await session.scalars(
                select(DNSRecord)
                .options(joinedload(DNSRecord.zone))
                .where(
                    (DNSRecord.http_check_enabled.is_(True)
                     | DNSRecord.http_check_direct_enabled.is_(True)),
                    DNSRecord.record_type.in_(ELIGIBLE_RECORD_TYPES),
                )
            )
        )
        semaphore = asyncio.Semaphore(HTTP_CONCURRENCY)

        async def inspect(record: DNSRecord, direct: bool) -> HTTPCheck | None:
            async with semaphore:
                if direct:
                    return await inspect_record(record, direct=True)
                return await inspect_record(record)

        targets = [
            (record, direct)
            for record in records
            for direct, enabled in (
                (False, record.http_check_enabled),
                (True, record.http_check_direct_enabled and record.proxied),
            )
            if enabled
        ]
        inspected = await asyncio.gather(
            *(inspect(record, direct) for record, direct in targets), return_exceptions=True
        )
        checked = failed = 0
        for (record, direct), check in zip(targets, inspected, strict=True):
            if check is None:
                continue
            try:
                if isinstance(check, BaseException):
                    raise check
                if direct:
                    await store_record_result(session, record, check, direct=True)
                else:
                    await store_record_result(session, record, check)
                checked += 1
                failed += check.status != "healthy"
            except Exception:
                await session.rollback()
                failed += 1
        from app.config import get_settings

        await prune_check_logs(session, get_settings().check_log_retention_days)
        return checked, failed
