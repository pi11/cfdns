from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AppSettings
from app.security import TokenCipher


def validate_proxy_url(proxy_url: str) -> str:
    value = proxy_url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
        raise ValueError("Proxy must be a valid HTTP, HTTPS, or SOCKS5 URL.")
    return value


async def global_proxy(session: AsyncSession, settings: Settings) -> str | None:
    app_settings = await session.get(AppSettings, 1)
    if not app_settings or not app_settings.encrypted_global_proxy:
        return None
    return TokenCipher(settings.encryption_key).decrypt(app_settings.encrypted_global_proxy)
