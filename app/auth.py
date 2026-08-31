import hashlib
import hmac

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import get_settings

COOKIE_NAME = "cfdns_session"
PUBLIC_PATHS = {"/health", "/login"}


def session_token(password: str, encryption_key: str) -> str:
    message = f"cfdns-admin:{password}".encode()
    return hmac.new(encryption_key.encode(), message, hashlib.sha256).hexdigest()


def is_authenticated(request: Request) -> bool:
    settings = get_settings()
    expected = session_token(settings.admin_password, settings.encryption_key)
    supplied = request.cookies.get(COOKIE_NAME, "")
    return hmac.compare_digest(supplied, expected)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/static/") or is_authenticated(request):
            return await call_next(request)
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse(f"/login?next={path}", status_code=303)
