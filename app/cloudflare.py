from __future__ import annotations

from typing import Any

import httpx


class CloudflareError(RuntimeError):
    pass


class CloudflareClient:
    def __init__(
        self,
        token: str,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        proxy_url: str | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30,
            transport=transport,
            proxy=proxy_url,
        )

    async def __aenter__(self) -> CloudflareClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CloudflareError(f"Cloudflare request failed: {exc}") from exc
        if not response.is_success or not data.get("success", False):
            errors = data.get("errors") or []
            message = "; ".join(str(item.get("message", item)) for item in errors)
            raise CloudflareError(message or f"Cloudflare returned HTTP {response.status_code}")
        return data

    async def verify_token(self) -> None:
        await self._request("GET", "/user/tokens/verify")

    async def _paginate(self, path: str) -> list[dict[str, Any]]:
        page = 1
        results: list[dict[str, Any]] = []
        while True:
            data = await self._request("GET", path, params={"page": page, "per_page": 100})
            results.extend(data.get("result") or [])
            info = data.get("result_info") or {}
            total_pages = int(info.get("total_pages") or 1)
            if page >= total_pages:
                return results
            page += 1

    async def list_zones(self) -> list[dict[str, Any]]:
        return await self._paginate("/zones")

    async def list_records(self, zone_id: str) -> list[dict[str, Any]]:
        return await self._paginate(f"/zones/{zone_id}/dns_records")

    async def create_record(self, zone_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._request("POST", f"/zones/{zone_id}/dns_records", json=payload)
        return data["result"]

    async def update_record(
        self, zone_id: str, record_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        data = await self._request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", json=payload)
        return data["result"]

    async def delete_record(self, zone_id: str, record_id: str) -> None:
        await self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
