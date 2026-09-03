import hashlib
import json

import httpx
import pytest

from app.ovh import OVHClient, parse_token
from app.ovh_services import normalize_service


def test_parse_token_supports_compact_and_json_formats() -> None:
    assert parse_token("app:secret:consumer") == ("app", "secret", "consumer")
    assert parse_token(
        json.dumps(
            {"applicationKey": "app", "applicationSecret": "secret", "consumerKey": "consumer"}
        )
    ) == ("app", "secret", "consumer")


@pytest.mark.asyncio
async def test_client_only_uses_get_and_enriches_vps_ips() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        responses = {
            "/services": [42],
            "/services/42": {
                "serviceId": 42,
                "resource": {"name": "vps-1", "product": {"name": "vps"}},
            },
            "/vps/vps-1": {"name": "vps-1"},
            "/vps/vps-1/ips": ["192.0.2.5"],
            "/dedicated/server": [],
        }
        return httpx.Response(200, json=responses[request.url.path])

    async with OVHClient(
        "app:secret:consumer", "https://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        services = await client.list_services()

    assert all(request.method == "GET" for request in requests)
    assert requests[0].headers["X-Ovh-Signature"].startswith("$1$")
    assert services[0]["productDetails"][-1] == ["192.0.2.5"]


@pytest.mark.asyncio
async def test_dedicated_servers_are_discovered_independently_of_product_label() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        responses = {
            "/services": [6, 7],
            "/services/6": {
                "serviceId": 6,
                "parentServiceId": 7,
                "resource": {
                    "name": "ns123.ip-192-0-2.net",
                    "displayName": "ns123.ip-192-0-2.net",
                    "product": {"name": "bandwidth-500"},
                },
            },
            "/services/7": {
                "serviceId": 7,
                "parentServiceId": None,
                "route": {
                    "path": "/dedicated/server/{serviceName}",
                    "url": "/dedicated/server/ns123.ip-192-0-2.net",
                },
                "resource": {
                    "name": "ns123.ip-192-0-2.net",
                    "displayName": "spree",
                    "product": {"name": "19rise01"},
                },
            },
            "/dedicated/server": ["ns123.ip-192-0-2.net"],
            "/dedicated/server/ns123.ip-192-0-2.net": {
                "name": "spree",
                "ip": "192.0.2.10",
                "datacenter": "bhs1",
            },
            "/dedicated/server/ns123.ip-192-0-2.net/ips": ["192.0.2.0/28"],
        }
        return httpx.Response(200, json=responses[request.url.path])

    async with OVHClient(
        "app:secret:consumer", "https://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        services = await client.list_services()

    addon = normalize_service(next(item for item in services if item["serviceId"] == 6))
    normalized = normalize_service(next(item for item in services if item["serviceId"] == 7))
    assert json.loads(addon["ips"]) == []
    assert normalized["name"] == "spree"
    assert normalized["canonical_name"] == "ns123.ip-192-0-2.net"
    assert len(services) == 2
    assert json.loads(normalized["ips"]) == ["192.0.2.0/28", "192.0.2.10/32"]


@pytest.mark.asyncio
async def test_signature_is_sha1_of_ovh_signature_components(monkeypatch) -> None:
    monkeypatch.setattr("app.ovh.time.time", lambda: 1_700_000_000)

    def handler(request: httpx.Request) -> httpx.Response:
        source = "+".join(
            (
                "secret",
                "consumer",
                "GET",
                "https://ca.example.test/me",
                "",
                "1700000000",
            )
        )
        assert request.headers["X-Ovh-Signature"] == "$1$" + hashlib.sha1(
            source.encode()
        ).hexdigest()
        return httpx.Response(200, json={"firstname": "Test"})

    async with OVHClient(
        "app:secret:consumer",
        "https://ca.example.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.verify_token()


def test_normalize_service_extracts_ip_ranges_and_price() -> None:
    normalized = normalize_service(
        {
            "serviceId": 42,
            "currentState": "active",
            "resource": {
                "name": "srv-1",
                "displayName": "Primary server",
                "product": {"name": "dedicatedServer"},
                "datacenter": "gra",
            },
            "billing": {"pricing": {"price": {"value": 1999, "currencyCode": "EUR"}}},
            "productDetails": [{"ip": "192.0.2.5"}, ["2001:db8::/64"]],
        }
    )
    assert normalized["name"] == "Primary server"
    assert normalized["service_type"] == "dedicatedServer"
    assert json.loads(normalized["ips"]) == ["192.0.2.5/32", "2001:db8::/64"]
    assert normalized["price"] == "1999 EUR"
