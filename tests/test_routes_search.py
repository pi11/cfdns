import pytest

from app.routes import MAX_BULK_RECORDS, bulk_delete_records, ping_selected_records, search_pattern


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("_dmarc", "%\\_dmarc%"),
        ("100%", "%100\\%%"),
        ("a\\b", "%a\\\\b%"),
        ("plain", "%plain%"),
    ],
)
def test_search_pattern_escapes_like_wildcards(query: str, expected: str) -> None:
    assert search_pattern(query) == expected


@pytest.mark.parametrize("endpoint", [bulk_delete_records, ping_selected_records])
@pytest.mark.asyncio
async def test_bulk_endpoints_reject_oversized_selections(endpoint) -> None:
    response = await endpoint(record_ids=list(range(MAX_BULK_RECORDS + 1)), session=None)

    assert response.status_code == 413
    assert b"at most" in response.body
