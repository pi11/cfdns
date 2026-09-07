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

from app.database import SessionLocal
from app.models import DNSRecord, HTTPCheckResult
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


async def inspect_record(record: DNSRecord) -> HTTPCheck | None:
    if record.record_type not in ELIGIBLE_RECORD_TYPES:
        return None
    return await inspect_url(record.http_check_url)


async def store_record_result(
    session: AsyncSession, record: DNSRecord, check: HTTPCheck, *, notify: bool = True
) -> None:
    await session.execute(delete(HTTPCheckResult).where(HTTPCheckResult.record_id == record.id))
    session.add(
        HTTPCheckResult(
            record_id=record.id,
            url=check.url,
            final_url=check.final_url,
            status=check.status,
            status_code=check.status_code,
            latency_ms=check.latency_ms,
            error=check.error,
            checked_at=datetime.now(UTC),
        )
    )
    await session.commit()
    if notify:
        from app.config import get_settings
        from app.notifications import notify_http_result

        await notify_http_result(session, record, check, get_settings())


async def check_and_store_record(session: AsyncSession, record: DNSRecord) -> HTTPCheck | None:
    check = await inspect_record(record)
    if check is not None:
        await store_record_result(session, record, check)
    return check


async def check_all_enabled_records() -> tuple[int, int]:
    async with SessionLocal() as session:
        records = list(
            await session.scalars(
                select(DNSRecord)
                .options(joinedload(DNSRecord.zone))
                .where(
                    DNSRecord.http_check_enabled.is_(True),
                    DNSRecord.record_type.in_(ELIGIBLE_RECORD_TYPES),
                )
            )
        )
        semaphore = asyncio.Semaphore(HTTP_CONCURRENCY)

        async def inspect(record: DNSRecord) -> HTTPCheck | None:
            async with semaphore:
                return await inspect_record(record)

        inspected = await asyncio.gather(
            *(inspect(record) for record in records), return_exceptions=True
        )
        checked = failed = 0
        for record, check in zip(records, inspected, strict=True):
            if check is None:
                continue
            try:
                if isinstance(check, BaseException):
                    raise check
                await store_record_result(session, record, check)
                checked += 1
                failed += check.status != "healthy"
            except Exception:
                await session.rollback()
                failed += 1
        return checked, failed
