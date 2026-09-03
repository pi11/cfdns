from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.atw_services import sync_all_atw_accounts
from app.config import Settings
from app.models import ATWAccount, ATWService, OVHAccount, OVHService
from app.ovh_services import sync_all_ovh_accounts

SyncProvider = Callable[[AsyncSession, Settings], Awaitable[None]]


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    description: str
    route: str
    account_model: type
    service_model: type
    sync_all: SyncProvider


PROVIDERS = (
    Provider(
        id="ovh",
        label="OVH",
        description="Dedicated servers, VPS, IP addresses, and pricing.",
        route="/ovh",
        account_model=OVHAccount,
        service_model=OVHService,
        sync_all=sync_all_ovh_accounts,
    ),
    Provider(
        id="atw",
        label="ATW",
        description="Customer services, VPS inventory, addresses, and pricing.",
        route="/atw",
        account_model=ATWAccount,
        service_model=ATWService,
        sync_all=sync_all_atw_accounts,
    ),
)


def provider(provider_id: str) -> Provider:
    return next(item for item in PROVIDERS if item.id == provider_id)
