from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
from itertools import groupby
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.atw import ATWClient, ATWError
from app.atw_services import sync_atw_account
from app.auth import COOKIE_NAME, session_token
from app.cloudflare import CloudflareClient, CloudflareError
from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    Account,
    AppSettings,
    ATWAccount,
    ATWService,
    DNSRecord,
    OVHAccount,
    OVHService,
    PingCheckResult,
    PingNotificationState,
    SSLCheckResult,
    SSLNotificationState,
    Zone,
)
from app.ovh import OVHClient, OVHError
from app.ovh_services import sync_ovh_account
from app.ping_checker import (
    PING_CONCURRENCY,
    check_and_store_record as check_and_store_ping_record,
    inspect_record as inspect_ping_record,
    store_record_results as store_ping_results,
)
from app.providers import PROVIDERS
from app.proxy import global_proxy, validate_proxy_url
from app.schemas import DNSRecordInput
from app.security import TokenCipher
from app.services import apply_remote_record, sync_account
from app.ssl_checker import ELIGIBLE_RECORD_TYPES, check_and_store_record
from app.telegram import TelegramClient, TelegramError

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


async def provider_context(session: AsyncSession) -> dict[str, object]:
    integrations = []
    for item in PROVIDERS:
        account_count = int(
            await session.scalar(select(func.count()).select_from(item.account_model)) or 0
        )
        integrations.append(
            {
                "id": item.id,
                "label": item.label,
                "description": item.description,
                "route": item.route,
                "account_count": account_count,
                "enabled": account_count > 0,
            }
        )
    return {
        "integrations": integrations,
        "enabled_integrations": [item for item in integrations if item["enabled"]],
    }


async def render_template(
    request: Request, session: AsyncSession, name: str, context: dict | None = None
):
    values = await provider_context(session)
    values.update(context or {})
    return templates.TemplateResponse(request, name, values)


async def load_app_settings(session: AsyncSession) -> AppSettings:
    app_settings = await session.get(AppSettings, 1)
    if app_settings is None:
        app_settings = AppSettings(id=1)
        session.add(app_settings)
        await session.flush()
    return app_settings


async def ovh_matches_for_records(
    session: AsyncSession, records: list[DNSRecord]
) -> dict[int, list[OVHService]]:
    if not await session.scalar(select(OVHAccount.id).limit(1)):
        return {}
    services = list(
        await session.scalars(
            select(OVHService).options(joinedload(OVHService.account)).order_by(OVHService.name)
        )
    )
    networks = []
    for service in services:
        for value in service.ip_list:
            try:
                networks.append((ipaddress.ip_network(value, strict=False), service))
            except ValueError:
                continue
    matches: dict[int, list[OVHService]] = {}
    for record in records:
        if record.record_type not in {"A", "AAAA"}:
            continue
        try:
            address = ipaddress.ip_address(record.content.strip())
        except ValueError:
            continue
        found = [service for network, service in networks if address in network]
        if found:
            matches[record.id] = list(dict.fromkeys(found))
    return matches


async def atw_matches_for_records(
    session: AsyncSession, records: list[DNSRecord]
) -> dict[int, list[ATWService]]:
    if not await session.scalar(select(ATWAccount.id).limit(1)):
        return {}
    services = list(
        await session.scalars(
            select(ATWService).options(joinedload(ATWService.account)).order_by(ATWService.name)
        )
    )
    networks = []
    for service in services:
        for value in service.ip_list:
            try:
                networks.append((ipaddress.ip_network(value, strict=False), service))
            except ValueError:
                continue
    matches = {}
    for record in records:
        if record.record_type not in {"A", "AAAA"}:
            continue
        try:
            address = ipaddress.ip_address(record.content.strip())
        except ValueError:
            continue
        found = [service for network, service in networks if address in network]
        if found:
            matches[record.id] = list(dict.fromkeys(found))
    return matches


@router.get("/health")
async def health(session: AsyncSession = Depends(get_db)):
    await session.execute(select(1))
    return {"status": "ok"}


def redirect(path: str, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {key: value for key, value in {"message": message, "error": error}.items() if value}
    target = f"{path}?{urlencode(params)}" if params else path
    return RedirectResponse(target, status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": next, "error": request.query_params.get("error")},
    )


@router.post("/login")
async def login(
    password: str = Form(),
    next: str = Form("/"),
    settings: Settings = Depends(get_settings),
):
    if not hmac.compare_digest(password, settings.admin_password):
        return redirect("/login", error="Incorrect password.")
    destination = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        session_token(settings.admin_password, settings.encryption_key),
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


async def load_record(session: AsyncSession, record_id: int) -> DNSRecord:
    record = await session.scalar(
        select(DNSRecord)
        .options(
            joinedload(DNSRecord.zone).joinedload(Zone.account),
            selectinload(DNSRecord.ssl_results),
            selectinload(DNSRecord.ping_results),
        )
        .where(DNSRecord.id == record_id)
    )
    if not record:
        raise HTTPException(404, "DNS record not found")
    return record


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    q: str = Query(default="", max_length=300),
    account_id: str = "",
    zone_id: str = "",
    record_type: str = "",
    proxy_status: str = "",
    ssl_status: str = "",
    page: int = Query(default=1, ge=1),
    per_page: str = "25",
    session: AsyncSession = Depends(get_db),
):
    if per_page not in {"25", "50", "100", "all"}:
        per_page = "25"
    if proxy_status not in {"", "proxied", "dns_only"}:
        proxy_status = ""
    if ssl_status not in {"", "error"}:
        ssl_status = ""
    selected_account_id = int(account_id) if account_id.isdigit() else None
    selected_zone_id = int(zone_id) if zone_id.isdigit() else None
    accounts = list(
        await session.scalars(
            select(Account).options(selectinload(Account.zones)).order_by(Account.name)
        )
    )
    conditions = []
    if q:
        pattern = f"%{q}%"
        conditions.append(
            or_(
                DNSRecord.name.ilike(pattern),
                DNSRecord.content.ilike(pattern),
                DNSRecord.cloudflare_comment.ilike(pattern),
                DNSRecord.local_comment.ilike(pattern),
                Zone.name.ilike(pattern),
            )
        )
    if selected_account_id:
        conditions.append(Zone.account_id == selected_account_id)
    if selected_zone_id:
        conditions.append(DNSRecord.zone_id == selected_zone_id)
    if record_type:
        conditions.append(DNSRecord.record_type == record_type.upper())
    if proxy_status == "proxied":
        conditions.append(DNSRecord.proxied.is_(True))
    elif proxy_status == "dns_only":
        conditions.append(DNSRecord.proxied.is_not(True))

    ssl_error_condition = DNSRecord.ssl_results.any(SSLCheckResult.status != "valid")
    ssl_error_count = int(
        await session.scalar(
            select(func.count(SSLCheckResult.id))
            .join(SSLCheckResult.record)
            .join(DNSRecord.zone)
            .where(*conditions, SSLCheckResult.status != "valid")
        )
        or 0
    )
    if ssl_status == "error":
        conditions.append(ssl_error_condition)

    total_records = int(
        await session.scalar(
            select(func.count(DNSRecord.id)).join(DNSRecord.zone).where(*conditions)
        )
        or 0
    )
    page_size = total_records or 1 if per_page == "all" else int(per_page)
    total_pages = max(1, (total_records + page_size - 1) // page_size)
    page = min(page, total_pages)

    statement = (
        select(DNSRecord)
        .join(DNSRecord.zone)
        .options(
            joinedload(DNSRecord.zone).joinedload(Zone.account),
            selectinload(DNSRecord.ssl_results),
            selectinload(DNSRecord.ping_results),
        )
        .where(*conditions)
        .order_by(
            Zone.name,
            Zone.account_id,
            Zone.id,
            DNSRecord.name,
            DNSRecord.record_type,
            DNSRecord.id,
        )
    )
    if per_page != "all":
        statement = statement.offset((page - 1) * page_size).limit(page_size)
    records = list(await session.scalars(statement))
    ovh_matches = await ovh_matches_for_records(session, records)
    atw_matches = await atw_matches_for_records(session, records)
    zone_groups = [
        list(group) for _zone_id, group in groupby(records, key=lambda record: record.zone_id)
    ]

    def page_url(target_page: int) -> str:
        params = {
            "q": q,
            "account_id": selected_account_id or "",
            "zone_id": selected_zone_id or "",
            "record_type": record_type,
            "proxy_status": proxy_status,
            "ssl_status": ssl_status,
            "per_page": per_page,
            "page": target_page,
        }
        return f"/?{urlencode(params)}"

    first_page_link = max(1, page - 2)
    last_page_link = min(total_pages, page + 2)
    page_links = [
        (number, page_url(number)) for number in range(first_page_link, last_page_link + 1)
    ]
    first_record = 0 if total_records == 0 else (page - 1) * page_size + 1
    last_record = total_records if per_page == "all" else min(page * page_size, total_records)

    def proxy_filter_url(status: str) -> str:
        params = {
            "q": q,
            "account_id": selected_account_id or "",
            "zone_id": selected_zone_id or "",
            "record_type": record_type,
            "proxy_status": "" if proxy_status == status else status,
            "ssl_status": ssl_status,
            "per_page": per_page,
            "page": 1,
        }
        return f"/?{urlencode(params)}"

    def account_filter_url(target_account_id: int) -> str:
        params = {
            "q": q,
            "account_id": "" if selected_account_id == target_account_id else target_account_id,
            "zone_id": "",
            "record_type": record_type,
            "proxy_status": proxy_status,
            "ssl_status": ssl_status,
            "per_page": per_page,
            "page": 1,
        }
        return f"/?{urlencode(params)}"

    def ssl_error_filter_url() -> str:
        params = {
            "q": q,
            "account_id": selected_account_id or "",
            "zone_id": selected_zone_id or "",
            "record_type": record_type,
            "proxy_status": proxy_status,
            "ssl_status": "" if ssl_status == "error" else "error",
            "per_page": per_page,
            "page": 1,
        }
        return f"/?{urlencode(params)}"

    context = {
        "request": request,
        "accounts": accounts,
        "records": records,
        "ovh_matches": ovh_matches,
        "atw_matches": atw_matches,
        "zone_groups": zone_groups,
        "expand_zone_groups": bool(q),
        "q": q,
        "account_id": selected_account_id,
        "zone_id": selected_zone_id,
        "record_type": record_type,
        "proxy_status": proxy_status,
        "ssl_status": ssl_status,
        "ssl_error_count": ssl_error_count,
        "ssl_error_filter_url": ssl_error_filter_url(),
        "proxy_filter_urls": {
            "proxied": proxy_filter_url("proxied"),
            "dns_only": proxy_filter_url("dns_only"),
        },
        "account_filter_urls": {account.id: account_filter_url(account.id) for account in accounts},
        "per_page": per_page,
        "page": page,
        "total_pages": total_pages,
        "total_records": total_records,
        "first_record": first_record,
        "last_record": last_record,
        "page_links": page_links,
        "previous_url": page_url(page - 1) if page > 1 else None,
        "next_url": page_url(page + 1) if page < total_pages else None,
        "ssl_eligible_types": ELIGIBLE_RECORD_TYPES,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    }
    template = "partials/records.html" if request.headers.get("HX-Request") else "index.html"
    if template == "partials/records.html":
        return templates.TemplateResponse(request, template, context)
    return await render_template(request, session, template, context)


@router.post("/accounts")
async def add_account(
    name: str = Form(min_length=1, max_length=120),
    api_token: str = Form(min_length=1),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if await session.scalar(select(Account).where(Account.name == name.strip())):
        return redirect("/", error="An account with this name already exists.")
    try:
        proxy = await global_proxy(session, settings)
        async with CloudflareClient(
            api_token.strip(), settings.cloudflare_api_base, proxy_url=proxy
        ) as client:
            await client.verify_token()
        account = Account(
            name=name.strip(),
            encrypted_token=TokenCipher(settings.encryption_key).encrypt(api_token.strip()),
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        await sync_account(session, account, settings)
    except (CloudflareError, ValueError) as exc:
        await session.rollback()
        return redirect("/", error=str(exc))
    return redirect("/", message=f"Account {account.name} connected and synchronized.")


@router.post("/accounts/{account_id}/sync")
async def synchronize_account(
    account_id: int,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    account = await session.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    try:
        await sync_account(session, account, settings)
    except Exception as exc:
        return redirect("/", error=f"Synchronization failed: {exc}")
    return redirect("/", message=f"Account {account.name} synchronized.")


@router.post("/accounts/{account_id}/delete")
async def delete_account(account_id: int, session: AsyncSession = Depends(get_db)):
    account = await session.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    await session.delete(account)
    await session.commit()
    return redirect("/", message="Account and its local cache were removed.")


@router.get("/zones/{zone_id}/records/new", response_class=HTMLResponse)
async def new_record_form(request: Request, zone_id: int, session: AsyncSession = Depends(get_db)):
    zone = await session.scalar(
        select(Zone).options(joinedload(Zone.account)).where(Zone.id == zone_id)
    )
    if not zone:
        raise HTTPException(404, "Zone not found")
    return await render_template(
        request, session, "record_form.html", {"zone": zone, "record": None}
    )


@router.post("/zones/{zone_id}/records")
async def create_record(
    zone_id: int,
    record_type: str = Form(alias="type"),
    name: str = Form(),
    content: str = Form(),
    ttl: int = Form(1),
    proxied: bool = Form(False),
    cloudflare_comment: str = Form(""),
    local_comment: str = Form(""),
    priority: int | None = Form(None),
    ssl_check_enabled: bool = Form(False),
    ping_check_enabled: bool = Form(False),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    zone = await session.scalar(
        select(Zone).options(joinedload(Zone.account)).where(Zone.id == zone_id)
    )
    if not zone:
        raise HTTPException(404, "Zone not found")
    data = DNSRecordInput(
        type=record_type,
        name=name,
        content=content,
        ttl=ttl,
        proxied=proxied,
        comment=cloudflare_comment or None,
        priority=priority,
    )
    token = TokenCipher(settings.encryption_key).decrypt(zone.account.encrypted_token)
    proxy = await global_proxy(session, settings)
    try:
        async with CloudflareClient(token, settings.cloudflare_api_base, proxy_url=proxy) as client:
            remote = await client.create_record(zone.cloudflare_id, data.cloudflare_payload())
        record = DNSRecord(
            zone=zone, cloudflare_id=remote["id"], local_comment=local_comment or None
        )
        apply_remote_record(record, remote)
        record.ssl_check_enabled = (
            ssl_check_enabled and record.record_type in ELIGIBLE_RECORD_TYPES and not record.proxied
        )
        record.ping_check_enabled = (
            ping_check_enabled and record.record_type in ELIGIBLE_RECORD_TYPES
        )
        session.add(record)
        await session.commit()
        if record.ssl_check_enabled:
            await check_and_store_record(session, record)
        if record.ping_check_enabled:
            await check_and_store_ping_record(session, record)
    except CloudflareError as exc:
        return redirect(f"/zones/{zone_id}/records/new", error=str(exc))
    return redirect("/", message=f"DNS record {record.name} created.")


@router.get("/records/{record_id}/edit", response_class=HTMLResponse)
async def edit_record_form(
    request: Request, record_id: int, session: AsyncSession = Depends(get_db)
):
    record = await load_record(session, record_id)
    return await render_template(
        request,
        session,
        "record_form.html",
        {"zone": record.zone, "record": record, "error": request.query_params.get("error")},
    )


@router.post("/records/{record_id}")
async def update_record(
    record_id: int,
    record_type: str = Form(alias="type"),
    name: str = Form(),
    content: str = Form(),
    ttl: int = Form(1),
    proxied: bool = Form(False),
    cloudflare_comment: str = Form(""),
    local_comment: str = Form(""),
    priority: int | None = Form(None),
    ssl_check_enabled: bool = Form(False),
    ping_check_enabled: bool = Form(False),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    record = await load_record(session, record_id)
    data = DNSRecordInput(
        type=record_type,
        name=name,
        content=content,
        ttl=ttl,
        proxied=proxied,
        comment=cloudflare_comment or None,
        priority=priority,
    )
    token = TokenCipher(settings.encryption_key).decrypt(record.zone.account.encrypted_token)
    proxy = await global_proxy(session, settings)
    try:
        async with CloudflareClient(token, settings.cloudflare_api_base, proxy_url=proxy) as client:
            remote = await client.update_record(
                record.zone.cloudflare_id, record.cloudflare_id, data.cloudflare_payload()
            )
        apply_remote_record(record, remote)
        record.local_comment = local_comment or None
        record.ssl_check_enabled = (
            ssl_check_enabled and record.record_type in ELIGIBLE_RECORD_TYPES and not record.proxied
        )
        record.ping_check_enabled = (
            ping_check_enabled and record.record_type in ELIGIBLE_RECORD_TYPES
        )
        if not record.ssl_check_enabled:
            await session.execute(
                delete(SSLCheckResult).where(SSLCheckResult.record_id == record.id)
            )
        if not record.ping_check_enabled:
            await session.execute(
                delete(PingCheckResult).where(PingCheckResult.record_id == record.id)
            )
            await session.execute(
                delete(PingNotificationState).where(PingNotificationState.record_id == record.id)
            )
        await session.commit()
        if record.ssl_check_enabled:
            await check_and_store_record(session, record)
        if record.ping_check_enabled:
            await check_and_store_ping_record(session, record)
    except CloudflareError as exc:
        return redirect(f"/records/{record_id}/edit", error=str(exc))
    return redirect("/", message=f"DNS record {record.name} updated.")


@router.post("/records/{record_id}/ssl-toggle")
async def toggle_ssl_check(
    request: Request,
    record_id: int,
    enabled: bool = Form(False),
    session: AsyncSession = Depends(get_db),
):
    record = await load_record(session, record_id)
    record.ssl_check_enabled = (
        enabled and record.record_type in ELIGIBLE_RECORD_TYPES and not record.proxied
    )
    if record.ssl_check_enabled:
        await session.commit()
        await check_and_store_record(session, record)
    else:
        await session.execute(delete(SSLCheckResult).where(SSLCheckResult.record_id == record.id))
        await session.commit()
    await session.refresh(record, ["ssl_results"])
    if request.headers.get("HX-Request"):
        current_url = request.headers.get("HX-Current-URL", "/")
        current_params = parse_qs(urlparse(current_url).query)
        current_proxy_status = current_params.get("proxy_status", [""])[0]

        def proxy_url(status: str) -> str:
            params = {
                "q": current_params.get("q", [""])[0],
                "account_id": current_params.get("account_id", [""])[0],
                "zone_id": current_params.get("zone_id", [""])[0],
                "record_type": current_params.get("record_type", [""])[0],
                "proxy_status": "" if current_proxy_status == status else status,
                "per_page": current_params.get("per_page", ["25"])[0],
                "page": 1,
            }
            return f"/?{urlencode(params)}"

        return templates.TemplateResponse(
            request,
            "partials/record_row.html",
            {
                "record": record,
                "ovh_matches": await ovh_matches_for_records(session, [record]),
                "atw_matches": await atw_matches_for_records(session, [record]),
                "ssl_eligible_types": ELIGIBLE_RECORD_TYPES,
                "proxy_status": current_proxy_status,
                "proxy_filter_urls": {
                    "proxied": proxy_url("proxied"),
                    "dns_only": proxy_url("dns_only"),
                },
            },
        )
    state = "enabled" if record.ssl_check_enabled else "disabled"
    return redirect("/", message=f"SSL checks {state} for {record.name}.")


@router.post("/records/{record_id}/ping-toggle")
async def toggle_ping_check(
    request: Request,
    record_id: int,
    enabled: bool = Form(False),
    session: AsyncSession = Depends(get_db),
):
    record = await load_record(session, record_id)
    record.ping_check_enabled = enabled and record.record_type in ELIGIBLE_RECORD_TYPES
    if record.ping_check_enabled:
        await session.commit()
        await check_and_store_ping_record(session, record)
    else:
        await session.execute(delete(PingCheckResult).where(PingCheckResult.record_id == record.id))
        await session.execute(
            delete(PingNotificationState).where(PingNotificationState.record_id == record.id)
        )
        await session.commit()
    await session.refresh(record, ["ssl_results", "ping_results"])
    if request.headers.get("HX-Request"):
        current_params = parse_qs(
            urlparse(request.headers.get("HX-Current-URL", "/")).query
        )
        current_proxy_status = current_params.get("proxy_status", [""])[0]

        def proxy_url(status: str) -> str:
            params = {
                "q": current_params.get("q", [""])[0],
                "account_id": current_params.get("account_id", [""])[0],
                "zone_id": current_params.get("zone_id", [""])[0],
                "record_type": current_params.get("record_type", [""])[0],
                "proxy_status": "" if current_proxy_status == status else status,
                "per_page": current_params.get("per_page", ["25"])[0],
                "page": 1,
            }
            return f"/?{urlencode(params)}"

        return templates.TemplateResponse(
            request,
            "partials/record_row.html",
            {
                "record": record,
                "ovh_matches": await ovh_matches_for_records(session, [record]),
                "atw_matches": await atw_matches_for_records(session, [record]),
                "ssl_eligible_types": ELIGIBLE_RECORD_TYPES,
                "proxy_status": current_proxy_status,
                "proxy_filter_urls": {
                    "proxied": proxy_url("proxied"),
                    "dns_only": proxy_url("dns_only"),
                },
            },
        )
    state = "enabled" if record.ping_check_enabled else "disabled"
    return redirect("/", message=f"Ping checks {state} for {record.name}.")


@router.get("/ovh", response_class=HTMLResponse)
async def ovh_dashboard(
    request: Request,
    q: str = Query(default="", max_length=300),
    account_id: str = "",
    hide_included: bool = False,
    session: AsyncSession = Depends(get_db),
):
    app_settings = await load_app_settings(session)
    hide_included = hide_included or app_settings.hide_included_services
    selected_account_id = int(account_id) if account_id.isdigit() else None
    accounts = list(
        await session.scalars(
            select(OVHAccount).options(selectinload(OVHAccount.services)).order_by(OVHAccount.name)
        )
    )
    conditions = []
    if selected_account_id:
        conditions.append(OVHService.account_id == selected_account_id)
    if q:
        pattern = f"%{q}%"
        conditions.append(
            or_(
                OVHService.name.ilike(pattern),
                OVHService.canonical_name.ilike(pattern),
                OVHService.service_type.ilike(pattern),
                OVHService.ips.ilike(pattern),
                OVHService.region.ilike(pattern),
            )
        )
    services = list(
        await session.scalars(
            select(OVHService)
            .options(joinedload(OVHService.account))
            .where(*conditions)
            .order_by(OVHService.account_id, OVHService.name)
        )
    )
    if hide_included:
        services = [service for service in services if not service.has_zero_price]
    service_groups = [
        list(group)
        for _account_id, group in groupby(services, key=lambda service: service.account_id)
    ]

    def ovh_account_filter_url(target_account_id: int) -> str:
        params = {
            "q": q,
            "account_id": "" if selected_account_id == target_account_id else target_account_id,
        }
        if hide_included:
            params["hide_included"] = "true"
        return f"/ovh?{urlencode(params)}"

    return await render_template(
        request,
        session,
        "ovh.html",
        {
            "accounts": accounts,
            "account_color_indexes": {
                account.id: index % 6 for index, account in enumerate(accounts)
            },
            "account_colors": {
                account.id: (
                    "#60a5fa",
                    "#34d399",
                    "#f59e0b",
                    "#c084fc",
                    "#fb7185",
                    "#22d3ee",
                )[index % 6]
                for index, account in enumerate(accounts)
            },
            "services": services,
            "service_groups": service_groups,
            "expand_account_groups": bool(q or selected_account_id),
            "account_filter_urls": {
                account.id: ovh_account_filter_url(account.id) for account in accounts
            },
            "q": q,
            "account_id": selected_account_id,
            "hide_included": hide_included,
            "hide_included_locked": app_settings.hide_included_services,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    config: Settings = Depends(get_settings),
):
    app_settings = await load_app_settings(session)
    await session.commit()
    cipher = TokenCipher(config.encryption_key)
    telegram_proxy = (
        cipher.decrypt(app_settings.encrypted_telegram_proxy)
        if app_settings.encrypted_telegram_proxy
        else ""
    )
    configured_global_proxy = (
        cipher.decrypt(app_settings.encrypted_global_proxy)
        if app_settings.encrypted_global_proxy
        else ""
    )
    return await render_template(
        request,
        session,
        "settings.html",
        {
            "settings": app_settings,
            "telegram_proxy": telegram_proxy,
            "global_proxy": configured_global_proxy,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/settings/preferences")
async def save_preferences(
    hide_included_services: bool = Form(False), session: AsyncSession = Depends(get_db)
):
    app_settings = await load_app_settings(session)
    app_settings.hide_included_services = hide_included_services
    await session.commit()
    return redirect("/settings", message="Preferences saved.")


@router.post("/settings/proxy")
async def save_global_proxy(
    proxy_url: str = Form(""),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    app_settings = await load_app_settings(session)
    if proxy_url.strip():
        try:
            proxy = validate_proxy_url(proxy_url)
        except ValueError as exc:
            return redirect("/settings", error=str(exc))
        app_settings.encrypted_global_proxy = TokenCipher(settings.encryption_key).encrypt(proxy)
    else:
        app_settings.encrypted_global_proxy = None
    await session.commit()
    return redirect("/settings", message="Global API proxy saved.")


@router.post("/settings/telegram")
async def save_telegram(
    bot_token: str = Form(""),
    chat_id: str = Form(""),
    proxy_url: str = Form(""),
    clear_proxy: bool = Form(False),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    app_settings = await load_app_settings(session)
    cipher = TokenCipher(settings.encryption_key)
    if bot_token.strip():
        effective_token = bot_token.strip()
    elif app_settings.encrypted_telegram_token:
        effective_token = cipher.decrypt(app_settings.encrypted_telegram_token)
    else:
        return redirect("/settings", error="Bot token is required when connecting a bot.")
    if clear_proxy:
        specific_proxy = None
    elif proxy_url.strip():
        try:
            specific_proxy = validate_proxy_url(proxy_url)
        except ValueError as exc:
            return redirect("/settings", error=str(exc))
    elif app_settings.encrypted_telegram_proxy:
        specific_proxy = cipher.decrypt(app_settings.encrypted_telegram_proxy)
    else:
        specific_proxy = None
    request_proxy = specific_proxy or await global_proxy(session, settings)
    try:
        async with TelegramClient(effective_token, request_proxy) as client:
            bot = await client.get_me()
    except (TelegramError, ValueError) as exc:
        return redirect("/settings", error=str(exc))
    app_settings.encrypted_telegram_token = cipher.encrypt(effective_token)
    app_settings.encrypted_telegram_proxy = (
        cipher.encrypt(specific_proxy) if specific_proxy else None
    )
    app_settings.telegram_bot_username = bot.get("username")
    app_settings.telegram_chat_id = chat_id.strip() or None
    await session.commit()
    return redirect("/settings", message=f"Telegram bot @{bot.get('username', 'unknown')} saved.")


@router.post("/settings/telegram/remove")
async def remove_telegram_bot(session: AsyncSession = Depends(get_db)):
    app_settings = await load_app_settings(session)
    app_settings.encrypted_telegram_token = None
    app_settings.encrypted_telegram_proxy = None
    app_settings.telegram_bot_username = None
    app_settings.telegram_chat_id = None
    await session.execute(delete(SSLNotificationState))
    await session.execute(delete(PingNotificationState))
    await session.commit()
    return redirect("/settings", message="Telegram bot and notification state removed.")


@router.post("/settings/telegram/detect-admin")
async def detect_telegram_admin(
    session: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
):
    app_settings = await load_app_settings(session)
    if not app_settings.encrypted_telegram_token:
        return redirect("/settings", error="Save a Telegram bot first.")
    token = TokenCipher(settings.encryption_key).decrypt(app_settings.encrypted_telegram_token)
    proxy = (
        TokenCipher(settings.encryption_key).decrypt(app_settings.encrypted_telegram_proxy)
        if app_settings.encrypted_telegram_proxy
        else await global_proxy(session, settings)
    )
    try:
        async with TelegramClient(token, proxy) as client:
            admin = await client.latest_start_chat()
    except TelegramError as exc:
        return redirect("/settings", error=str(exc))
    if not admin:
        return redirect(
            "/settings", error="No /start message found. Send /start to the bot and try again."
        )
    app_settings.telegram_chat_id = admin[0]
    await session.commit()
    return redirect("/settings", message=f"Telegram admin {admin[1]} connected.")


@router.post("/settings/telegram/test")
async def test_telegram(
    session: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
):
    app_settings = await load_app_settings(session)
    if not app_settings.encrypted_telegram_token or not app_settings.telegram_chat_id:
        return redirect("/settings", error="Connect a Telegram bot and admin first.")
    token = TokenCipher(settings.encryption_key).decrypt(app_settings.encrypted_telegram_token)
    proxy = (
        TokenCipher(settings.encryption_key).decrypt(app_settings.encrypted_telegram_proxy)
        if app_settings.encrypted_telegram_proxy
        else await global_proxy(session, settings)
    )
    try:
        async with TelegramClient(token, proxy) as client:
            await client.send_message(app_settings.telegram_chat_id, "CFDNS test notification ✅")
    except TelegramError as exc:
        return redirect("/settings", error=str(exc))
    return redirect("/settings", message="Test notification sent.")


@router.post("/ovh/accounts")
async def add_ovh_account(
    name: str = Form(min_length=1, max_length=120),
    endpoint: str = Form("ovh-eu"),
    application_key: str = Form(min_length=1),
    application_secret: str = Form(min_length=1),
    consumer_key: str = Form(min_length=1),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    clean_name = name.strip()
    if endpoint not in {"ovh-eu", "ovh-ca"}:
        return redirect("/ovh", error="Invalid OVH API region.")
    if await session.scalar(select(OVHAccount).where(OVHAccount.name == clean_name)):
        return redirect("/ovh", error="An OVH account with this name already exists.")
    try:
        api_token = json.dumps(
            {
                "applicationKey": application_key.strip(),
                "applicationSecret": application_secret.strip(),
                "consumerKey": consumer_key.strip(),
            }
        )
        api_base = settings.ovh_ca_api_base if endpoint == "ovh-ca" else settings.ovh_api_base
        proxy = await global_proxy(session, settings)
        async with OVHClient(api_token, api_base, proxy_url=proxy) as client:
            await client.verify_token()
        account = OVHAccount(
            name=clean_name,
            encrypted_token=TokenCipher(settings.encryption_key).encrypt(api_token),
            endpoint=endpoint,
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        await sync_ovh_account(session, account, settings)
    except (OVHError, ValueError) as exc:
        await session.rollback()
        return redirect("/ovh", error=str(exc))
    return redirect("/ovh", message=f"OVH account {account.name} connected and synchronized.")


@router.post("/ovh/accounts/{account_id}/sync")
async def synchronize_ovh_account(
    account_id: int,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    account = await session.get(OVHAccount, account_id)
    if not account:
        raise HTTPException(404, "OVH account not found")
    try:
        await sync_ovh_account(session, account, settings)
    except Exception as exc:
        return redirect("/ovh", error=f"Synchronization failed: {exc}")
    return redirect("/ovh", message=f"OVH account {account.name} synchronized.")


@router.post("/ovh/accounts/{account_id}/delete")
async def delete_ovh_account(account_id: int, session: AsyncSession = Depends(get_db)):
    account = await session.get(OVHAccount, account_id)
    if not account:
        raise HTTPException(404, "OVH account not found")
    await session.delete(account)
    await session.commit()
    return redirect("/ovh", message="OVH account and its local cache were removed.")


@router.get("/atw", response_class=HTMLResponse)
async def atw_dashboard(
    request: Request,
    q: str = Query(default="", max_length=300),
    account_id: str = "",
    session: AsyncSession = Depends(get_db),
):
    selected_account_id = int(account_id) if account_id.isdigit() else None
    accounts = list(
        await session.scalars(
            select(ATWAccount).options(selectinload(ATWAccount.services)).order_by(ATWAccount.name)
        )
    )
    conditions = []
    if selected_account_id:
        conditions.append(ATWService.account_id == selected_account_id)
    if q:
        pattern = f"%{q}%"
        conditions.append(
            or_(
                ATWService.name.ilike(pattern),
                ATWService.service_type.ilike(pattern),
                ATWService.ips.ilike(pattern),
                ATWService.customer_name.ilike(pattern),
            )
        )
    services = list(
        await session.scalars(
            select(ATWService)
            .options(joinedload(ATWService.account))
            .where(*conditions)
            .order_by(ATWService.account_id, ATWService.name)
        )
    )

    def account_url(target_id: int) -> str:
        params = {"q": q, "account_id": "" if selected_account_id == target_id else target_id}
        return f"/atw?{urlencode(params)}"

    return await render_template(
        request,
        session,
        "atw.html",
        {
            "accounts": accounts,
            "services": services,
            "service_groups": [
                list(group)
                for _account_id, group in groupby(services, key=lambda item: item.account_id)
            ],
            "account_id": selected_account_id,
            "account_filter_urls": {account.id: account_url(account.id) for account in accounts},
            "expand_account_groups": bool(q or selected_account_id),
            "q": q,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/atw/accounts")
async def add_atw_account(
    name: str = Form(min_length=1, max_length=120),
    username: str = Form(min_length=1, max_length=253),
    api_token: str = Form(min_length=1),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    clean_name = name.strip()
    clean_username = username.strip()
    if await session.scalar(select(ATWAccount).where(ATWAccount.name == clean_name)):
        return redirect("/atw", error="An ATW account with this name already exists.")
    try:
        proxy = await global_proxy(session, settings)
        async with ATWClient(
            api_token.strip(), settings.atw_api_base, clean_username, proxy_url=proxy
        ) as client:
            await client.verify_token(clean_username)
        account = ATWAccount(
            name=clean_name,
            username=clean_username,
            encrypted_token=TokenCipher(settings.encryption_key).encrypt(api_token.strip()),
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        await sync_atw_account(session, account, settings)
    except (ATWError, ValueError) as exc:
        await session.rollback()
        return redirect("/atw", error=str(exc))
    return redirect("/atw", message=f"ATW account {account.name} connected and synchronized.")


@router.post("/atw/accounts/{account_id}/sync")
async def synchronize_atw_account(
    account_id: int,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    account = await session.get(ATWAccount, account_id)
    if not account:
        raise HTTPException(404, "ATW account not found")
    try:
        await sync_atw_account(session, account, settings)
    except Exception as exc:
        return redirect("/atw", error=f"Synchronization failed: {exc}")
    return redirect("/atw", message=f"ATW account {account.name} synchronized.")


@router.post("/atw/accounts/{account_id}/delete")
async def delete_atw_account(account_id: int, session: AsyncSession = Depends(get_db)):
    account = await session.get(ATWAccount, account_id)
    if not account:
        raise HTTPException(404, "ATW account not found")
    await session.delete(account)
    await session.commit()
    return redirect("/atw", message="ATW account and its local cache were removed.")


@router.post("/records/{record_id}/delete")
async def delete_record(
    request: Request,
    record_id: int,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    record = await load_record(session, record_id)
    token = TokenCipher(settings.encryption_key).decrypt(record.zone.account.encrypted_token)
    proxy = await global_proxy(session, settings)
    try:
        async with CloudflareClient(token, settings.cloudflare_api_base, proxy_url=proxy) as client:
            await client.delete_record(record.zone.cloudflare_id, record.cloudflare_id)
    except CloudflareError as exc:
        if request.headers.get("HX-Request"):
            return PlainTextResponse(str(exc), status_code=502)
        return redirect("/", error=str(exc))
    await session.delete(record)
    await session.commit()
    if request.headers.get("HX-Request"):
        return Response(status_code=200)
    return redirect("/", message="DNS record deleted.")


@router.post("/records/actions/bulk-delete")
async def bulk_delete_records(
    record_ids: list[int] = Form(),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    unique_ids = list(dict.fromkeys(record_ids))
    if not unique_ids:
        return JSONResponse({"deleted_ids": [], "errors": []})

    records = list(
        await session.scalars(
            select(DNSRecord)
            .options(joinedload(DNSRecord.zone).joinedload(Zone.account))
            .where(DNSRecord.id.in_(unique_ids))
        )
    )
    semaphore = asyncio.Semaphore(5)
    proxy = await global_proxy(session, settings)

    async def delete_from_cloudflare(record: DNSRecord) -> tuple[int, str, str | None]:
        try:
            token = TokenCipher(settings.encryption_key).decrypt(
                record.zone.account.encrypted_token
            )
            async with semaphore:
                async with CloudflareClient(
                    token, settings.cloudflare_api_base, proxy_url=proxy
                ) as client:
                    await client.delete_record(record.zone.cloudflare_id, record.cloudflare_id)
            return record.id, record.name, None
        except (CloudflareError, ValueError) as exc:
            return record.id, record.name, str(exc)

    results = await asyncio.gather(*(delete_from_cloudflare(record) for record in records))
    deleted_ids = [record_id for record_id, _name, error in results if error is None]
    errors = [
        {"record_id": record_id, "name": name, "error": error}
        for record_id, name, error in results
        if error is not None
    ]
    found_ids = {record.id for record in records}
    errors.extend(
        {"record_id": record_id, "name": f"Record #{record_id}", "error": "Not found"}
        for record_id in unique_ids
        if record_id not in found_ids
    )
    if deleted_ids:
        await session.execute(delete(DNSRecord).where(DNSRecord.id.in_(deleted_ids)))
        await session.commit()
    return JSONResponse({"deleted_ids": deleted_ids, "errors": errors})


@router.post("/records/actions/ping")
async def ping_selected_records(
    record_ids: list[int] = Form(),
    session: AsyncSession = Depends(get_db),
):
    unique_ids = list(dict.fromkeys(record_ids))
    records = list(
        await session.scalars(
            select(DNSRecord)
            .options(joinedload(DNSRecord.zone))
            .where(
                DNSRecord.id.in_(unique_ids),
                DNSRecord.record_type.in_(ELIGIBLE_RECORD_TYPES),
            )
        )
    )
    semaphore = asyncio.Semaphore(PING_CONCURRENCY)
    inspected = await asyncio.gather(
        *(inspect_ping_record(record, semaphore) for record in records), return_exceptions=True
    )
    results = []
    for record, checks in zip(records, inspected, strict=True):
        if isinstance(checks, BaseException):
            results.append({"record_id": record.id, "name": record.name, "error": str(checks)})
            continue
        await store_ping_results(session, record, checks, notify=record.ping_check_enabled)
        results.append(
            {
                "record_id": record.id,
                "name": record.name,
                "reachable": sum(check.status == "reachable" for check in checks),
                "total": len(checks),
                "error": None,
            }
        )
    found_ids = {record.id for record in records}
    results.extend(
        {
            "record_id": record_id,
            "name": f"Record #{record_id}",
            "error": "Not found or not pingable",
        }
        for record_id in unique_ids
        if record_id not in found_ids
    )
    return JSONResponse({"results": results})
