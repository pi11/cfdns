import json

import httpx
import pytest

from app.atw import ATWClient, ATWError
from app.atw_services import normalize_atw_service


@pytest.mark.asyncio
async def test_atw_client_uses_x_token_and_merges_vps_details() -> None:
    requests = []

    def envelope(data, count=None):
        response = {"data": data}
        if count is not None:
            response["count"] = count
        return {"code": 200, "message": "ok", "response": response}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        paths = {
            "/users/admin%40example.com/customers": envelope([{"id": 4, "name": "ACME"}], 1),
            "/customers/4/services": envelope(
                [{"id": 9, "customer_id": 4, "product_id": "VPS", "price": 10}], 1
            ),
            "/customers/4/vps": envelope([{"id": 2, "service_id": 9}], 1),
            "/customers/4/vps/2": {
                "code": 200,
                "message": "ok",
                "response": {
                    "id": 2,
                    "service_id": 9,
                    "custom_name": "Production",
                    "addresses": [{"ip": "192.0.2.8"}],
                },
            },
        }
        path = request.url.raw_path.decode().split("?")[0].removeprefix("/api")
        return httpx.Response(200, json=paths[path])

    async with ATWClient(
        "token",
        "https://atw.test/api",
        "admin@example.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        services = await client.list_services("admin@example.com")

    assert all(request.method == "GET" for request in requests)
    assert all(request.headers["X-Token"] == "token" for request in requests)
    assert all(request.headers["X-API-Username"] == "admin@example.com" for request in requests)
    assert services[0]["resourceDetails"][0]["custom_name"] == "Production"


def test_atw_normalization_extracts_vps_addresses_and_price() -> None:
    result = normalize_atw_service(
        {
            "id": 9,
            "customer_id": 4,
            "product_id": "VPS-4CPU",
            "state": "active",
            "price": 12.5,
            "price_currency": "EUR",
            "customer": {"id": 4, "name": "ACME"},
            "resourceDetails": [
                {
                    "custom_name": "Production",
                    "state": "ON",
                    "interfaces": [{"target": "192.0.2.8 2001:db8::/64"}],
                    "addresses": [
                        {"ip": "198.51.100.4/32", "nexthop": "0.0.0.0/0"},
                        {"ip": "2001:db8::/64", "nexthop": "2001:db8::1"},
                    ],
                }
            ],
        }
    )
    assert result["name"] == "Production"
    assert result["price"] == "12.5 EUR"
    assert json.loads(result["ips"]) == ["198.51.100.4/32", "2001:db8::/64"]
    assert "0.0.0.0/0" not in json.loads(result["ips"])


@pytest.mark.asyncio
async def test_atw_errors_are_readable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": 403, "message": "Forbidden", "response": {}})

    async with ATWClient(
        "bad",
        "https://atw.test/api",
        "admin@example.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ATWError, match="Forbidden"):
            await client.verify_token("admin@example.com")
