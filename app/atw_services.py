from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.atw import ATWClient
from app.config import Settings
from app.models import ATWAccount, ATWService
from app.proxy import global_proxy
from app.security import TokenCipher


def normalize_atw_service(remote: dict[str, Any]) -> dict[str, Any]:
    details = remote.get("resourceDetails") or []
    resource = details[0] if details else {}
    customer = remote.get("customer") or {}
    ips: set[str] = set()
    for detail in details:
        addresses = detail.get("addresses") if isinstance(detail, dict) else []
        for address in addresses or []:
            candidate = address.get("ip") if isinstance(address, dict) else None
            if not isinstance(candidate, str):
                continue
            try:
                ips.add(str(ipaddress.ip_network(candidate, strict=False)))
            except ValueError:
                continue
    price_value = remote.get("price")
    currency = remote.get("price_currency") or remote.get("invoice_currency") or ""
    price = f"{price_value} {currency}".strip() if price_value is not None else None
    name = resource.get("custom_name") or resource.get("name") or remote.get("info")
    return {
        "atw_id": str(remote["id"]),
        "customer_id": str(remote.get("customer_id") or customer.get("id") or ""),
        "customer_name": customer.get("name"),
        "name": str(name or remote.get("product_id") or f"Service {remote['id']}"),
        "service_type": str(remote.get("product_id") or remote.get("product_group") or "unknown"),
        "status": resource.get("state") or remote.get("state"),
        "region": resource.get("datacenter") or resource.get("zone"),
        "ips": json.dumps(sorted(ips)),
        "price": price,
        "raw_json": json.dumps(remote, separators=(",", ":"), default=str),
    }


async def sync_atw_account(
    session: AsyncSession, account: ATWAccount, settings: Settings
) -> None:
    token = TokenCipher(settings.encryption_key).decrypt(account.encrypted_token)
    proxy = await global_proxy(session, settings)
    try:
        async with ATWClient(
            token, settings.atw_api_base, account.username, proxy_url=proxy
        ) as client:
            remote_services = await client.list_services(account.username)
        existing = {
            item.atw_id: item
            for item in (
                await session.scalars(
                    select(ATWService).where(ATWService.account_id == account.id)
                )
            ).all()
        }
        remote_ids = set()
        for remote in remote_services:
            values = normalize_atw_service(remote)
            remote_ids.add(values["atw_id"])
            service = existing.get(values["atw_id"])
            if service is None:
                service = ATWService(account_id=account.id, atw_id=values["atw_id"])
                session.add(service)
            for key, value in values.items():
                setattr(service, key, value)
        stale = [item.id for key, item in existing.items() if key not in remote_ids]
        if stale:
            await session.execute(delete(ATWService).where(ATWService.id.in_(stale)))
        account.last_synced_at = datetime.now(UTC)
        account.last_sync_error = None
        await session.commit()
    except Exception as exc:
        await session.rollback()
        current = await session.get(ATWAccount, account.id)
        if current:
            current.last_sync_error = str(exc)[:2000]
            await session.commit()
        raise


async def sync_all_atw_accounts(session: AsyncSession, settings: Settings) -> None:
    for account_id in list(await session.scalars(select(ATWAccount.id))):
        account = await session.get(ATWAccount, account_id)
        if account:
            try:
                await sync_atw_account(session, account, settings)
            except Exception:
                continue
