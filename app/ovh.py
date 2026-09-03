from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import quote

import httpx


class OVHError(RuntimeError):
    pass


def parse_token(token: str) -> tuple[str, str, str]:
    """Parse one pasteable OVH credential without storing its parts separately."""
    value = token.strip()
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        parts = (
            data.get("applicationKey") or data.get("application_key"),
            data.get("applicationSecret") or data.get("application_secret"),
            data.get("consumerKey") or data.get("consumer_key"),
        )
    else:
        parts = tuple(value.split(":", 2))
    if len(parts) != 3 or not all(isinstance(part, str) and part for part in parts):
        raise ValueError(
            "OVH API token must be applicationKey:applicationSecret:consumerKey "
            "or a JSON object containing those fields."
        )
    return parts  # type: ignore[return-value]


class OVHClient:
    """Minimal, deliberately read-only OVH API client."""

    def __init__(
        self, token: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.application_key, self.application_secret, self.consumer_key = parse_token(token)
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30, transport=transport)

    async def __aenter__(self) -> OVHClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        timestamp = str(int(time.time()))
        signature_source = "+".join(
            (self.application_secret, self.consumer_key, "GET", url, "", timestamp)
        )
        signature = "$1$" + hashlib.sha1(signature_source.encode()).hexdigest()
        headers = {
            "X-Ovh-Application": self.application_key,
            "X-Ovh-Consumer": self.consumer_key,
            "X-Ovh-Signature": signature,
            "X-Ovh-Timestamp": timestamp,
        }
        try:
            response = await self._client.get(path, headers=headers)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OVHError(f"OVH request failed: {exc}") from exc
        if not response.is_success:
            message = data.get("message") if isinstance(data, dict) else None
            raise OVHError(message or f"OVH returned HTTP {response.status_code}")
        return data

    async def verify_token(self) -> None:
        await self.get("/me")

    async def list_services(self) -> list[dict[str, Any]]:
        service_ids = await self.get("/services")
        if not isinstance(service_ids, list):
            raise OVHError("OVH returned an invalid services response")
        services = []
        for service_id in service_ids:
            service = await self.get(f"/services/{service_id}")
            if not isinstance(service, dict):
                continue
            # The generic service API contains billing metadata but product IPs live in
            # product-specific read endpoints. Enrich the response where possible.
            resource = service.get("resource") or {}
            product = resource.get("product") or {}
            product_name = product.get("name", "") if isinstance(product, dict) else str(product)
            name = resource.get("name")
            details: list[Any] = []
            if name:
                encoded = quote(str(name), safe="")
                paths = []
                lowered = product_name.lower()
                if "dedicated" in lowered:
                    paths = [f"/dedicated/server/{encoded}", f"/dedicated/server/{encoded}/ips"]
                elif "vps" in lowered:
                    paths = [f"/vps/{encoded}", f"/vps/{encoded}/ips"]
                elif "cloud" in lowered:
                    paths = [f"/cloud/project/{encoded}/instance"]
                elif "hosting" in lowered:
                    paths = [f"/hosting/web/{encoded}"]
                for path in paths:
                    try:
                        details.append(await self.get(path))
                    except OVHError:
                        # Permissions and available sub-resources vary by product.
                        continue
            service["productDetails"] = details
            services.append(service)

        # Dedicated servers are often labelled as Bare Metal in the generic service
        # response, which is not sufficient to infer their product API route. Discover
        # them explicitly and merge their details and assigned IP blocks by service name.
        try:
            dedicated_names = await self.get("/dedicated/server")
        except OVHError:
            dedicated_names = []
        if isinstance(dedicated_names, list):
            for dedicated_name in dedicated_names:
                name = str(dedicated_name)
                encoded = quote(name, safe="")
                details = []
                for path in (f"/dedicated/server/{encoded}", f"/dedicated/server/{encoded}/ips"):
                    try:
                        details.append(await self.get(path))
                    except OVHError:
                        continue
                server_info = next((item for item in details if isinstance(item, dict)), {})
                aliases = {name}
                for key in ("name", "serviceName", "reverse"):
                    if server_info.get(key):
                        aliases.add(str(server_info[key]))
                candidates = [
                    item
                    for item in services
                    if {
                        str((item.get("resource") or {}).get("name") or ""),
                        str((item.get("resource") or {}).get("displayName") or ""),
                    }
                    & aliases
                ]

                def dedicated_score(
                    item: dict[str, Any], server_name: str = name
                ) -> tuple[int, int]:
                    route = item.get("route") or {}
                    route_path = str(route.get("path") or "")
                    route_url = str(route.get("url") or "")
                    is_dedicated_route = route_path.startswith("/dedicated/server/")
                    route_names_server = route_url.rstrip("/").endswith(f"/{server_name}")
                    is_parent = item.get("parentServiceId") is None
                    return (int(is_dedicated_route and route_names_server), int(is_parent))

                service = max(candidates, key=dedicated_score) if candidates else None
                if service is None:
                    service = {
                        "serviceId": f"dedicated:{name}",
                        "currentState": "active",
                        "resource": {
                            "name": name,
                            "displayName": name,
                            "product": {"name": "dedicatedServer"},
                        },
                        "productDetails": [],
                    }
                    services.append(service)
                else:
                    # Preserve the stable endpoint name independently of OVH's editable
                    # display name so future syncs continue to join the same server.
                    service.setdefault("resource", {})["canonicalName"] = name
                service.setdefault("productDetails", []).extend(details)
        return services
