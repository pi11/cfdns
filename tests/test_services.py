from app.models import DNSRecord
from app.services import apply_remote_record


def test_remote_update_preserves_local_comment() -> None:
    record = DNSRecord(
        zone_id=1,
        cloudflare_id="record-id",
        record_type="A",
        name="old.example.com",
        content="192.0.2.1",
        local_comment="Do not remove",
    )
    apply_remote_record(
        record,
        {
            "id": "record-id",
            "type": "AAAA",
            "name": "new.example.com",
            "content": "2001:db8::1",
            "ttl": 300,
            "proxied": False,
            "proxiable": True,
            "comment": "Remote note",
        },
    )

    assert record.content == "2001:db8::1"
    assert record.cloudflare_comment == "Remote note"
    assert record.local_comment == "Do not remove"
