from datetime import UTC, datetime, timedelta

import pytest

from app.models import OVHService
from app.notifications import notification_state, ovh_expiration_notification_state
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


@pytest.mark.parametrize(
    ("days", "stage"),
    [(31, "healthy"), (30, ":30"), (14, ":14"), (7, ":7"), (1, ":1")],
)
def test_ovh_manual_renewal_notification_stages(days: int, stage: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = OVHService(
        account_id=1,
        ovh_id="42",
        name="server-1",
        service_type="dedicatedServer",
        expires_at=now + timedelta(days=days),
        auto_renew=False,
    )
    state, message = ovh_expiration_notification_state(service, now)

    if stage == "healthy":
        assert state == "healthy"
        assert message is None
    else:
        assert state.endswith(stage)
        assert message and f"expires in {days} day(s)" in message


def test_ovh_auto_renewal_does_not_alert() -> None:
    now = datetime.now(UTC)
    service = OVHService(
        account_id=1,
        ovh_id="42",
        name="server-1",
        service_type="dedicatedServer",
        expires_at=now + timedelta(days=1),
        auto_renew=True,
    )

    assert ovh_expiration_notification_state(service, now) == ("not-actionable", None)


@pytest.mark.parametrize(
    ("days", "stage"),
    [(45, ":detected"), (30, ":30"), (14, ":14"), (7, ":7"), (1, ":1")],
)
def test_ovh_scheduled_cancellation_notification_stages(days: int, stage: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = OVHService(
        account_id=1,
        ovh_id="43",
        name="server-to-cancel",
        service_type="dedicatedServer",
        expires_at=now + timedelta(days=60),
        auto_renew=True,
        cancellation_scheduled=True,
        cancellation_at=now + timedelta(days=days),
    )

    state, message = ovh_expiration_notification_state(service, now)

    assert state.endswith(stage)
    assert message and "cancellation scheduled" in message
    assert f"in {days} day(s)" in message
