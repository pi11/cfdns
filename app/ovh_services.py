from __future__ import annotations

import ipaddress
import json
from datetime import UTC, date, datetime, time
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


def _parse_ovh_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _find_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in keys and child is not None:
                return child
        for child in value.values():
            found = _find_value(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, keys)
            if found is not None:
                return found
    return None


def _expiration_and_renewal(remote: dict[str, Any]) -> tuple[datetime | None, bool | None]:
    service_info = remote.get("serviceInfo") or {}
    billing = remote.get("billing") or {}
    expiration_value = (
        service_info.get("expiration")
        or service_info.get("expirationDate")
        or _find_value(billing, {"expiration", "expirationdate"})
    )
    expires_at = _parse_ovh_date(expiration_value)

    renew = service_info.get("renew") or _find_value(billing, {"renew"})
    if isinstance(renew, dict) and isinstance(renew.get("automatic"), bool):
        return expires_at, renew["automatic"]
    renewal_type = service_info.get("renewalType") or _find_value(
        billing, {"renewaltype", "renewalmode", "mode"}
    )
    if isinstance(renewal_type, str):
        normalized = renewal_type.lower()
        if normalized.startswith("automatic") or normalized in {"auto", "autorenew"}:
            return expires_at, True
        if normalized in {"manual", "oneshot", "one-shot"}:
            return expires_at, False
    termination_policy = _find_value(billing, {"terminationpolicy"})
    if isinstance(termination_policy, str) and "terminate" in termination_policy.lower():
        return expires_at, False
    return expires_at, None


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
    expires_at, auto_renew = _expiration_and_renewal(remote)
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
        "expires_at": expires_at,
        "auto_renew": auto_renew,
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
        current_services = list(
            await session.scalars(
                select(OVHService).where(OVHService.account_id == account.id)
            )
        )
        from app.notifications import notify_ovh_expirations

        await notify_ovh_expirations(session, account, current_services, settings)
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
