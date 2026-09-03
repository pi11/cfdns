from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import OVHAccount, OVHService
from app.ovh import OVHClient
from app.proxy import global_proxy
from app.security import TokenCipher


def _walk(value: Any, key: str = ""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _walk(child, child_key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key)
    else:
        yield key.lower(), value


def normalize_service(remote: dict[str, Any]) -> dict[str, Any]:
    resource = remote.get("resource") or {}
    billing = remote.get("billing") or {}
    plan = billing.get("plan") or {}
    route = remote.get("route") or {}
    ips: set[str] = set()
    for _key, value in _walk(remote):
        if not isinstance(value, str):
            continue
        try:
            ips.add(str(ipaddress.ip_network(value, strict=False)))
        except ValueError:
            try:
                ips.add(str(ipaddress.ip_address(value)))
            except ValueError:
                pass
    pricing = billing.get("pricing") or {}
    price_value = plan.get("price") or billing.get("price") or pricing.get("price")
    if isinstance(price_value, dict):
        amount = price_value.get("text") or price_value.get("value")
        currency = price_value.get("currencyCode") or price_value.get("currency") or ""
        price = f"{amount} {currency}".strip() if amount is not None else None
    else:
        price = str(price_value) if price_value is not None else None
    service_id = remote.get("serviceId") or remote.get("id") or resource.get("name")
    name = resource.get("displayName") or resource.get("name") or str(service_id)
    product = resource.get("product") or {}
    product_name = product.get("name") if isinstance(product, dict) else product
    service_type = (
        product_name
        or resource.get("type")
        or route.get("path")
        or plan.get("code")
        or "unknown"
    )
    return {
        "ovh_id": str(service_id),
        "name": str(name),
        "canonical_name": str(resource.get("canonicalName") or resource.get("name"))
        if resource.get("canonicalName") or resource.get("name")
        else None,
        "service_type": str(service_type).strip("/") or "unknown",
        "status": remote.get("currentState") or resource.get("state") or remote.get("status"),
        "region": resource.get("region") or resource.get("datacenter") or remote.get("region"),
        "ips": json.dumps(sorted(ips)),
        "price": price,
        "raw_json": json.dumps(remote, separators=(",", ":"), default=str),
    }


async def sync_ovh_account(
    session: AsyncSession, account: OVHAccount, settings: Settings
) -> None:
    token = TokenCipher(settings.encryption_key).decrypt(account.encrypted_token)
    api_base = settings.ovh_ca_api_base if account.endpoint == "ovh-ca" else settings.ovh_api_base
    proxy = await global_proxy(session, settings)
    try:
        async with OVHClient(token, api_base, proxy_url=proxy) as client:
            remote_services = await client.list_services()
        existing = {
            item.ovh_id: item
            for item in (
                await session.scalars(
                    select(OVHService).where(OVHService.account_id == account.id)
                )
            ).all()
        }
        remote_ids: set[str] = set()
        for remote in remote_services:
            values = normalize_service(remote)
            remote_ids.add(values["ovh_id"])
            service = existing.get(values["ovh_id"])
            if service is None:
                service = OVHService(account_id=account.id, ovh_id=values["ovh_id"])
                session.add(service)
            for key, value in values.items():
                setattr(service, key, value)
        stale = [item.id for key, item in existing.items() if key not in remote_ids]
        if stale:
            await session.execute(delete(OVHService).where(OVHService.id.in_(stale)))
        account.last_synced_at = datetime.now(UTC)
        account.last_sync_error = None
        await session.commit()
    except Exception as exc:
        await session.rollback()
        current = await session.get(OVHAccount, account.id)
        if current:
            current.last_sync_error = str(exc)[:2000]
            await session.commit()
        raise


async def sync_all_ovh_accounts(session: AsyncSession, settings: Settings) -> None:
    for account_id in list(await session.scalars(select(OVHAccount.id))):
        account = await session.get(OVHAccount, account_id)
        if account:
            try:
                await sync_ovh_account(session, account, settings)
            except Exception:
                continue
