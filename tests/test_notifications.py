from datetime import UTC, datetime, timedelta

import pytest

from app.notifications import notification_state
from app.ssl_checker import EndpointCheck


@pytest.mark.parametrize(
    ("days", "stage"),
    [(31, "healthy"), (30, ":30"), (20, ":30"), (14, ":14"), (7, ":7"), (1, ":1")],
)
def test_expiry_notification_stages(days: int, stage: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state, message = notification_state(
        EndpointCheck("192.0.2.1", now + timedelta(days=days), "valid"), now
    )
    if stage == "healthy":
        assert state == "healthy"
        assert message is None
    else:
        assert state.endswith(stage)
        assert message and f"in {days} day(s)" in message


def test_failure_notification_state() -> None:
    state, message = notification_state(
        EndpointCheck("192.0.2.1", None, "connection_error", "Connection refused"),
        datetime.now(UTC),
    )
    assert state == "failure:connection_error"
    assert message and "Connection refused" in message
