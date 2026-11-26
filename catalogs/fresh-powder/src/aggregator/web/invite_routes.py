"""Invite code entry routes."""

from __future__ import annotations

import re
import secrets
import sqlite3

import aiosqlite
from deal_common.rate_limit import SlidingWindowRateLimiter, client_key
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from aggregator.auth import (
    SESSION_COOKIE,
    create_session_token,
    get_admin_key,
    is_public_mode,
    set_session_cookie,
)
from aggregator.auth_db import add_to_waitlist, redeem_invite_code
from aggregator.config import STORES
from aggregator.db import query_deals
from aggregator.web.seo import templates

invite_router = APIRouter()
INVITE_SUBMIT_LIMIT = 10
INVITE_WINDOW_SECONDS = 300
invite_submit_limiter = SlidingWindowRateLimiter(window_seconds=INVITE_WINDOW_SECONDS)
CAD_STORES = {s.name for s in STORES if s.currency == "CAD"}


async def _landing_context(error: str | None = None) -> dict:
    try:
        deal_count = await query_deals(count_only=True)
    except (sqlite3.Error, aiosqlite.Error, OSError, RuntimeError, ValueError):
        deal_count = 0
    try:
        sample_deals = await query_deals(
            min_discount=25, min_price=100, sort_by="discount_pct", limit=6,
        )
    except (sqlite3.Error, aiosqlite.Error, OSError, RuntimeError, ValueError):
        sample_deals = []
    return {
        "error": error,
        "deal_count": deal_count or 0,
        "store_count": len(STORES),
        "sample_deals": sample_deals,
        "cad_stores": CAD_STORES,
    }


@invite_router.get("/invite", response_class=HTMLResponse)
async def invite_page(request: Request):
    if is_public_mode():
        return RedirectResponse(url="/", status_code=302)
    ctx = await _landing_context()
    return templates.TemplateResponse(
        request=request, name="invite.html", context=ctx,
    )


@invite_router.post("/invite", response_class=HTMLResponse)
async def invite_submit(request: Request, code: str = Form(...)):
    if is_public_mode():
        return RedirectResponse(url="/", status_code=302)
    if not invite_submit_limiter.allow(
        client_key(request, "invite-submit"), INVITE_SUBMIT_LIMIT
    ):
        ctx = await _landing_context(error="Too many invite attempts. Please wait a few minutes and try again.")
        return templates.TemplateResponse(
            request=request, name="invite.html", context=ctx, status_code=429,
        )
    code = code.strip().upper()
    if not await redeem_invite_code(code):
        ctx = await _landing_context(error="Invalid or exhausted invite code.")
        return templates.TemplateResponse(
            request=request, name="invite.html", context=ctx,
        )
    token = create_session_token(code)
    response = RedirectResponse(url="/", status_code=302)
    set_session_cookie(request, response, SESSION_COOKIE, token)
    return response


@invite_router.post("/admin/login")
async def admin_login(request: Request, admin_key: str = Form(...)):
    configured_key = get_admin_key()
    if not configured_key or not secrets.compare_digest(admin_key.encode(), configured_key.encode()):
        return HTMLResponse("Invalid admin key.", status_code=403)
    response = RedirectResponse(url="/admin/codes", status_code=303)
    set_session_cookie(request, response, "admin_key", configured_key)
    return response


@invite_router.post("/waitlist")
async def waitlist_submit(request: Request, email: str = Form(...)):
    """Add an email to the waitlist."""
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()) or len(email) > 254:
        return JSONResponse({"ok": False, "error": "Please enter a valid email address."}, status_code=422)
    if not invite_submit_limiter.allow(client_key(request, "waitlist"), INVITE_SUBMIT_LIMIT):
        return JSONResponse({"ok": False, "error": "Please wait a few minutes and try again."}, status_code=429)
    try:
        await add_to_waitlist(email.strip())
    except (sqlite3.Error, OSError, RuntimeError, ValueError):
        return JSONResponse({"ok": False, "error": "We could not save your email. Please try again."}, status_code=503)
    return JSONResponse({"ok": True})
