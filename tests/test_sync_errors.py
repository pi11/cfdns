import pytest

from app import atw_services, ovh_services, services
from app.config import get_settings
from app.models import Account, ATWAccount, OVHAccount
from app.security import TokenCipher


class FailingClient:
    """Stands in for an integration whose API call fails mid-synchronization."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self) -> "FailingClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def list_zones(self):
        raise RuntimeError("upstream is down")

    async def list_services(self, *_args):
        raise RuntimeError("upstream is down")


def _token() -> str:
    return TokenCipher(get_settings().encryption_key).encrypt("k:s:c")


@pytest.mark.parametrize(
    ("module", "client_attribute", "model", "account_factory", "sync"),
    [
        (
            services,
            "CloudflareClient",
            Account,
            lambda token: Account(name="cf", encrypted_token=token),
            "sync_account",
        ),
        (
            ovh_services,
            "OVHClient",
            OVHAccount,
            lambda token: OVHAccount(name="ovh", encrypted_token=token),
            "sync_ovh_account",
        ),
        (
            atw_services,
            "ATWClient",
            ATWAccount,
            lambda token: ATWAccount(name="atw", username="user", encrypted_token=token),
            "sync_atw_account",
        ),
    ],
)
@pytest.mark.asyncio
async def test_failed_sync_records_the_original_error(
    session, monkeypatch, module, client_attribute, model, account_factory, sync
) -> None:
    monkeypatch.setattr(module, client_attribute, FailingClient)
    account = account_factory(_token())
    session.add(account)
    await session.commit()

    with pytest.raises(RuntimeError, match="upstream is down"):
        await getattr(module, sync)(session, account, get_settings())

    stored = await session.get(model, account.id)
    assert stored is not None
    assert stored.last_sync_error == "upstream is down"
