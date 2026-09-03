from __future__ import annotations

from typing import Any

import httpx

from app.proxy import validate_proxy_url


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(
        self,
        token: str,
        proxy_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        options: dict[str, Any] = {"timeout": 20, "transport": transport}
        if proxy_url:
            options["proxy"] = validate_proxy_url(proxy_url)
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}", **options
        )

    async def __aenter__(self) -> TelegramClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def _request(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._client.post(method, json=payload or {})
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # The bot token is part of Telegram's request URL; never echo transport
            # exceptions because their URL may expose the token in the UI or logs.
            raise TelegramError("Telegram request failed.") from exc
        if not response.is_success or not data.get("ok"):
            raise TelegramError(
                data.get("description") or f"Telegram returned HTTP {response.status_code}"
            )
        return data.get("result")

    async def get_me(self) -> dict[str, Any]:
        return await self._request("/getMe")

    async def latest_start_chat(self) -> tuple[str, str] | None:
        updates = await self._request("/getUpdates", {"limit": 100, "allowed_updates": ["message"]})
        for update in reversed(updates or []):
            message = update.get("message") or {}
            if str(message.get("text") or "").split()[0:1] == ["/start"]:
                chat = message.get("chat") or {}
                label = chat.get("username") or chat.get("first_name") or str(chat.get("id"))
                return str(chat["id"]), str(label)
        return None

    async def send_message(self, chat_id: str, text: str) -> None:
        await self._request("/sendMessage", {"chat_id": chat_id, "text": text})
