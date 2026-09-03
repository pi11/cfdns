from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import AuthenticationMiddleware
from app.config import get_settings
from app.database import SessionLocal
from app.providers import PROVIDERS
from app.routes import router
from app.services import sync_all_accounts


async def synchronization_loop() -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(settings.sync_interval_minutes * 60)
        async with SessionLocal() as session:
            await sync_all_accounts(session, settings)
            for provider in PROVIDERS:
                await provider.sync_all(session, settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(synchronization_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="CFDNS", lifespan=lifespan)
app.add_middleware(AuthenticationMiddleware)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)
