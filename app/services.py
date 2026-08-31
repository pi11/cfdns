from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloudflare import CloudflareClient
from app.config import Settings
from app.models import Account, DNSRecord, Zone
from app.security import TokenCipher


def apply_remote_record(record: DNSRecord, remote: dict) -> None:
    record.record_type = remote["type"]
    record.name = remote["name"]
    record.content = remote.get("content", "")
    record.ttl = remote.get("ttl", 1)
    record.proxied = remote.get("proxied")
    record.proxiable = remote.get("proxiable", False)
    record.cloudflare_comment = remote.get("comment")
    record.priority = remote.get("priority")
    record.data_json = json.dumps(remote.get("data")) if remote.get("data") else None
    record.synced_at = datetime.now(UTC)


async def sync_account(session: AsyncSession, account: Account, settings: Settings) -> None:
    token = TokenCipher(settings.encryption_key).decrypt(account.encrypted_token)
    try:
        async with CloudflareClient(token, settings.cloudflare_api_base) as client:
            remote_zones = await client.list_zones()
            existing_zones = {
                zone.cloudflare_id: zone
                for zone in (
                    await session.scalars(select(Zone).where(Zone.account_id == account.id))
                ).all()
            }
            remote_zone_ids: set[str] = set()
            for remote_zone in remote_zones:
                zone_id = remote_zone["id"]
                remote_zone_ids.add(zone_id)
                zone = existing_zones.get(zone_id)
                if zone is None:
                    zone = Zone(account_id=account.id, cloudflare_id=zone_id)
                    session.add(zone)
                zone.name = remote_zone["name"]
                zone.status = remote_zone.get("status", "unknown")
                await session.flush()
                await sync_zone(session, zone, client)

            stale_zones = [
                zone.id for key, zone in existing_zones.items() if key not in remote_zone_ids
            ]
            if stale_zones:
                await session.execute(delete(Zone).where(Zone.id.in_(stale_zones)))

        account.last_synced_at = datetime.now(UTC)
        account.last_sync_error = None
        await session.commit()
    except Exception as exc:
        await session.rollback()
        account = await session.get(Account, account.id)
        if account:
            account.last_sync_error = str(exc)[:2000]
            await session.commit()
        raise


async def sync_zone(session: AsyncSession, zone: Zone, client: CloudflareClient) -> None:
    remote_records = await client.list_records(zone.cloudflare_id)
    existing = {
        record.cloudflare_id: record
        for record in (
            await session.scalars(select(DNSRecord).where(DNSRecord.zone_id == zone.id))
        ).all()
    }
    remote_ids: set[str] = set()
    for remote in remote_records:
        remote_ids.add(remote["id"])
        record = existing.get(remote["id"])
        if record is None:
            record = DNSRecord(zone_id=zone.id, cloudflare_id=remote["id"])
            session.add(record)
        apply_remote_record(record, remote)
    stale_ids = [record.id for key, record in existing.items() if key not in remote_ids]
    if stale_ids:
        await session.execute(delete(DNSRecord).where(DNSRecord.id.in_(stale_ids)))


async def sync_all_accounts(session: AsyncSession, settings: Settings) -> None:
    account_ids = list(await session.scalars(select(Account.id)))
    for account_id in account_ids:
        account = await session.get(Account, account_id)
        if account:
            try:
                await sync_account(session, account, settings)
            except Exception:
                continue
