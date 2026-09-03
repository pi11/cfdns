import httpx
import pytest

from app.telegram import TelegramClient, validate_proxy_url


def test_proxy_validation() -> None:
    assert validate_proxy_url("http://user:pass@proxy.example:8080").startswith("http://")
    assert validate_proxy_url("socks5://127.0.0.1:1080").startswith("socks5://")
    with pytest.raises(ValueError, match="Proxy must"):
        validate_proxy_url("ftp://proxy.example/file")


@pytest.mark.asyncio
async def test_detects_latest_start_chat_and_sends_message() -> None:
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {"message": {"text": "/start", "chat": {"id": 123, "username": "admin"}}}
                    ],
                },
            )
        sent.append(request.read().decode())
        return httpx.Response(200, json={"ok": True, "result": {}})

    async with TelegramClient("secret", transport=httpx.MockTransport(handler)) as client:
        assert await client.latest_start_chat() == ("123", "admin")
        await client.send_message("123", "test")

    assert '"chat_id":"123"' in sent[0]
