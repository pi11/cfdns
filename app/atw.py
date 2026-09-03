from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class ATWError(RuntimeError):
    pass


class ATWClient:
    """Read-only client for ATW's X-Token API."""

    def __init__(
        self,
        token: str,
        base_url: str,
        username: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        proxy_url: str | None = None,
    ):
        headers = {"X-Token": token, "Accept": "application/json"}
        if username:
            headers["X-API-Username"] = username
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=30,
            transport=transport,
            proxy=proxy_url,
        )

    async def __aenter__(self) -> ATWClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._client.get(path, params=params)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ATWError(f"ATW request failed: {exc}") from exc
        if not response.is_success or int(data.get("code", response.status_code)) >= 400:
            detail = data.get("response") or {}
            message = detail.get("message") if isinstance(detail, dict) else None
            raise ATWError(
                message or data.get("message") or f"ATW returned HTTP {response.status_code}"
            )
        return data.get("response")

    async def list_items(self, path: str) -> list[dict[str, Any]]:
        offset = 0
        results: list[dict[str, Any]] = []
        while True:
            response = await self.get(path, {"limit": 300, "offset": offset})
            container = response if isinstance(response, dict) else {}
            items = container.get("data") if isinstance(container.get("data"), list) else []
            results.extend(item for item in items if isinstance(item, dict))
            count = int(container.get("count") or len(items))
            if len(items) < 300 or len(results) >= count:
                return results
            offset += len(items)

    async def list_customers(self, username: str) -> list[dict[str, Any]]:
        return await self.list_items(f"/users/{quote(username, safe='')}/customers")

    async def verify_token(self, username: str) -> None:
        await self.list_customers(username)

    async def list_services(self, username: str) -> list[dict[str, Any]]:
        customers = await self.list_customers(username)
        results = []
        for customer in customers:
            customer_id = customer["id"]
            services = await self.list_items(f"/customers/{customer_id}/services")
            by_id = {str(service.get("id")): service for service in services}
            try:
                vps_items = await self.list_items(f"/customers/{customer_id}/vps")
            except ATWError:
                vps_items = []
            for vps in vps_items:
                try:
                    detail = await self.get(f"/customers/{customer_id}/vps/{vps['id']}")
                    if isinstance(detail, dict):
                        vps = detail.get("data", detail)
                except (ATWError, KeyError):
                    pass
                service = by_id.get(str(vps.get("service_id")))
                if service is not None:
                    service.setdefault("resourceDetails", []).append(vps)
            for service in services:
                service["customer"] = customer
                results.append(service)
        return results
