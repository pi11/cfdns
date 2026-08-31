import httpx
import pytest

from app.cloudflare import CloudflareClient, CloudflareError


@pytest.mark.asyncio
async def test_zone_pagination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": [{"id": f"zone-{page}"}],
                "result_info": {"total_pages": 2},
            },
        )

    async with CloudflareClient(
        "token", "https://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        zones = await client.list_zones()

    assert [zone["id"] for zone in zones] == ["zone-1", "zone-2"]


@pytest.mark.asyncio
async def test_api_errors_are_readable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"success": False, "errors": [{"message": "Forbidden"}]})

    async with CloudflareClient(
        "token", "https://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(CloudflareError, match="Forbidden"):
            await client.verify_token()
