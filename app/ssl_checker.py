from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography import x509
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import SessionLocal
from app.models import DNSRecord, SSLCheckResult

ELIGIBLE_RECORD_TYPES = {"A", "AAAA", "CNAME"}
TLS_PORT = 443
CONNECT_TIMEOUT_SECONDS = 12


@dataclass(slots=True)
class EndpointCheck:
    ip_address: str
    certificate_expires_at: datetime | None
    status: str
    error: str | None = None


async def resolve_record_addresses(record_type: str, content: str) -> list[str]:
    if record_type in {"A", "AAAA"}:
        return [str(ipaddress.ip_address(content.strip()))]
    if record_type != "CNAME":
        return []

    loop = asyncio.get_running_loop()
    target = content.strip().rstrip(".")
    results = await loop.getaddrinfo(
        target,
        TLS_PORT,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return sorted({item[4][0] for item in results}, key=lambda value: ipaddress.ip_address(value))


async def _read_certificate(
    hostname: str, ip_address: str, context: ssl.SSLContext
) -> datetime | None:
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=ip_address,
                port=TLS_PORT,
                ssl=context,
                server_hostname=hostname,
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        ssl_object = writer.get_extra_info("ssl_object")
        certificate_der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        if not certificate_der:
            return None
        return x509.load_der_x509_certificate(certificate_der).not_valid_after_utc
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()


async def check_endpoint(hostname: str, ip_address: str) -> EndpointCheck:
    verified_context = ssl.create_default_context()
    try:
        expires_at = await _read_certificate(hostname, ip_address, verified_context)
        if expires_at is None:
            return EndpointCheck(
                ip_address, None, "invalid", "The peer did not provide a certificate."
            )
        status = "expired" if expires_at <= datetime.now(UTC) else "valid"
        return EndpointCheck(ip_address, expires_at, status)
    except ssl.SSLCertVerificationError as exc:
        unverified_context = ssl.create_default_context()
        unverified_context.check_hostname = False
        unverified_context.verify_mode = ssl.CERT_NONE
        try:
            expires_at = await _read_certificate(hostname, ip_address, unverified_context)
        except Exception:
            expires_at = None
        status = "expired" if expires_at and expires_at <= datetime.now(UTC) else "invalid"
        return EndpointCheck(ip_address, expires_at, status, str(exc))
    except (TimeoutError, OSError, ssl.SSLError) as exc:
        return EndpointCheck(ip_address, None, "connection_error", str(exc))


async def inspect_record(record: DNSRecord) -> list[EndpointCheck]:
    if record.record_type not in ELIGIBLE_RECORD_TYPES or record.proxied:
        return []
    try:
        addresses = await resolve_record_addresses(record.record_type, record.content)
    except (OSError, ValueError) as exc:
        return [EndpointCheck("unresolved", None, "resolution_error", str(exc))]
    if not addresses:
        return [EndpointCheck("unresolved", None, "resolution_error", "No IP addresses found.")]
    return list(await asyncio.gather(*(check_endpoint(record.name, ip) for ip in addresses)))


async def check_and_store_record(session: AsyncSession, record: DNSRecord) -> list[EndpointCheck]:
    checks = await inspect_record(record)
    await session.execute(delete(SSLCheckResult).where(SSLCheckResult.record_id == record.id))
    checked_at = datetime.now(UTC)
    session.add_all(
        SSLCheckResult(
            record_id=record.id,
            ip_address=check.ip_address,
            certificate_expires_at=check.certificate_expires_at,
            status=check.status,
            error=check.error,
            checked_at=checked_at,
        )
        for check in checks
    )
    await session.commit()
    from app.config import get_settings
    from app.notifications import notify_ssl_results

    await notify_ssl_results(session, record, checks, get_settings())
    return checks


async def check_all_enabled_records() -> tuple[int, int]:
    async with SessionLocal() as session:
        record_ids = list(
            await session.scalars(
                select(DNSRecord.id).where(
                    DNSRecord.ssl_check_enabled.is_(True),
                    DNSRecord.record_type.in_(ELIGIBLE_RECORD_TYPES),
                    DNSRecord.proxied.is_not(True),
                )
            )
        )
        checked = 0
        failed = 0
        for record_id in record_ids:
            record = await session.scalar(
                select(DNSRecord)
                .options(joinedload(DNSRecord.zone))
                .where(DNSRecord.id == record_id)
            )
            if not record:
                continue
            try:
                results = await check_and_store_record(session, record)
                checked += 1
                if any(result.status != "valid" for result in results):
                    failed += 1
            except Exception:
                await session.rollback()
                failed += 1
        return checked, failed
