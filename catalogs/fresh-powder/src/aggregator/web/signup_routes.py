"""Email-only prelaunch signup; no delivery or implicit waitlist migration."""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256

from deal_common.rate_limit import SlidingWindowRateLimiter, client_key
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from aggregator.auth_db import _sync
from aggregator.auth_db import alert_signup_db as signup_db
from aggregator.web.seo import templates

signup_router = APIRouter()
signup_limiter = SlidingWindowRateLimiter(3600)
CONSENT_VERSION = "rare-deals-prelaunch-v1"


def signup_error(request: Request, message: str, status: int, *, removal: bool = False):
    if "application/json" in request.headers.get("accept", ""):
        response = JSONResponse({"ok": False, "error": message}, status_code=status)
    else:
        response = templates.TemplateResponse(
            request=request,
            name="signup_result.html",
            status_code=status,
            context={
                "result_title": "Please try again",
                "message": message,
                "noindex": True,
                "retry_url": "/alerts/remove" if removal else "/alerts",
            },
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@signup_router.get("/alerts", response_class=HTMLResponse)
async def alerts(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="alerts.html",
        context={
            "page_title": "Rare Ski & Snowboard Deal Alerts — Coming Soon | FreshPowder",
            "page_description": "Join the email list for exceptional ski and snowboard deals. Alerts are coming soon. Free browsing, no account, no routine sale emails.",
        },
    )


@signup_router.post("/alerts/signup")
async def signup(request: Request, email: str = Form(""), website: str = Form("")):
    email = email.strip().lower()
    if website:
        return JSONResponse({"ok": True, "message": "Thanks for your interest."})
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return signup_error(request, "Please enter a valid email address.", 422)
    if not signup_limiter.allow(client_key(request, "alerts"), 10):
        return signup_error(request, "Please wait before trying again.", 429)
    token = secrets.token_urlsafe(32)
    try:
        conn = signup_db()
        conn.execute(
            "INSERT OR IGNORE INTO deal_alert_signups VALUES (?, ?, ?, ?)",
            (
                email,
                datetime.now(UTC).isoformat(),
                CONSENT_VERSION,
                sha256(token.encode()).hexdigest(),
            ),
        )
        inserted = conn.execute("SELECT changes()").fetchone()[0] == 1
        conn.commit()
        _sync()
    except (sqlite3.Error, OSError, RuntimeError, ValueError):
        return signup_error(request, "We could not save your email. Please try again.", 503)
    message = "You’re on the list. Rare-deal alerts are coming soon; no emails are being sent yet."
    context = {
        "message": message,
        "removal_token": token if inserted else None,
        "noindex": True,
    }
    if "application/json" in request.headers.get("accept", ""):
        response = JSONResponse({"ok": True, **context})
    else:
        response = templates.TemplateResponse(
            request=request, name="signup_result.html", context=context
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@signup_router.get("/alerts/remove", response_class=HTMLResponse)
async def removal_page(request: Request):
    response = templates.TemplateResponse(
        request=request, name="signup_remove.html", context={"noindex": True}
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@signup_router.post("/alerts/remove")
async def remove_signup(request: Request, token: str = Form("")):
    token = token.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return signup_error(request, "Use the complete private code from your removal link.", 422, removal=True)
    try:
        conn = signup_db()
        conn.execute("DELETE FROM deal_alert_signups WHERE removal_hash = ?", (sha256(token.encode()).hexdigest(),))
        conn.commit()
        _sync()
    except (sqlite3.Error, OSError, RuntimeError, ValueError):
        return signup_error(request, "We could not remove your signup. Please retry using your private link.", 503, removal=True)
    response = templates.TemplateResponse(
        request=request,
        name="signup_result.html",
        context={"result_title": "Your signup is removed", "message": "Your signup has been removed.", "noindex": True},
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
