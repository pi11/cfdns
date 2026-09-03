from __future__ import annotations

import math
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AppSettings, DNSRecord, SSLNotificationState
from app.security import TokenCipher
from app.ssl_checker import EndpointCheck
from app.telegram import TelegramClient, TelegramError

EXPIRY_THRESHOLDS = (1, 7, 14, 30)


def notification_state(check: EndpointCheck, now: datetime) -> tuple[str, str | None]:
    if check.status != "valid" or not check.certificate_expires_at:
        return f"failure:{check.status}", (
            f"🚨 SSL check failed\nIP: {check.ip_address}\nStatus: {check.status}"
            + (f"\nError: {check.error}" if check.error else "")
        )
    expires = check.certificate_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    days = math.ceil((expires - now).total_seconds() / 86400)
    threshold = next((value for value in EXPIRY_THRESHOLDS if days <= value), None)
    if threshold is None:
        return "healthy", None
    return f"expiry:{expires.date().isoformat()}:{threshold}", (
        f"⚠️ SSL certificate expires in {max(days, 0)} day(s)\n"
        f"IP: {check.ip_address}\nExpires: {expires:%Y-%m-%d %H:%M UTC}"
    )


async def notify_ssl_results(
    session: AsyncSession,
    record: DNSRecord,
    checks: list[EndpointCheck],
    settings: Settings,
) -> None:
    app_settings = await session.get(AppSettings, 1)
    if (
        not app_settings
        or not app_settings.encrypted_telegram_token
        or not app_settings.telegram_chat_id
    ):
        return
    token = TokenCipher(settings.encryption_key).decrypt(app_settings.encrypted_telegram_token)
    proxy = (
        TokenCipher(settings.encryption_key).decrypt(app_settings.encrypted_telegram_proxy)
        if app_settings.encrypted_telegram_proxy
        else None
    )
    now = datetime.now(UTC)
    async with TelegramClient(token, proxy) as client:
        for check in checks:
            state = await session.scalar(
                select(SSLNotificationState).where(
                    SSLNotificationState.record_id == record.id,
                    SSLNotificationState.ip_address == check.ip_address,
                )
            )
            new_key, detail = notification_state(check, now)
            old_key = state.state_key if state else None
            message = detail
            if old_key and old_key.startswith("failure:") and not new_key.startswith("failure:"):
                message = f"✅ SSL certificate recovered\nIP: {check.ip_address}"
                if detail:
                    message += f"\n\n{detail}"
            elif new_key == "healthy" and old_key and old_key != "healthy":
                message = f"✅ SSL certificate recovered or renewed\nIP: {check.ip_address}"
            if new_key == old_key:
                continue
            if message:
                prefix = f"Host: {record.name}\nZone: {record.zone.name}\n"
                try:
                    await client.send_message(app_settings.telegram_chat_id, prefix + message)
                except TelegramError:
                    continue
            if state is None:
                state = SSLNotificationState(
                    record_id=record.id, ip_address=check.ip_address, state_key=new_key
                )
                session.add(state)
            else:
                state.state_key = new_key
        await session.commit()
