"""Send rare-deal digest emails to subscribed addresses when Resend is configured."""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import sleep
from urllib.parse import urlencode

import httpx

ROOT = Path(__file__).resolve().parents[1]
for rel in ("common", "catalogs/fresh-powder/src"):
    sys.path.insert(0, str(ROOT / rel))

from aggregator.auth_db import _sync, alert_signup_db
from aggregator.db import _sqlite_uri
from aggregator.web.signup_routes import unsubscribe_signature


def qualifying_rare_deals(deals_db: Path, *, min_discount: float = 55.0, min_reference: float = 400.0) -> list[dict]:
    if not deals_db.is_file():
        return []
    cutoff = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=36)).isoformat()
    with sqlite3.connect(_sqlite_uri(deals_db, "ro"), uri=True) as conn:
        conn.row_factory = sqlite3.Row
        legacy_sent = set()
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sent_deal_alerts'").fetchone():
            legacy_sent = {row[0] for row in conn.execute("SELECT url FROM sent_deal_alerts")}
        rows = conn.execute(
            """
            SELECT d.store, d.name, d.url, d.current_price, d.original_price, d.discount_pct, d.sizes, d.currency
            FROM deals d
            WHERE d.exclusion_reason IS NULL
              AND d.scraped_at >= ?
              AND d.category IN ('skis', 'snowboards', 'ski boots', 'snowboard boots', 'bindings')
              AND d.original_price >= ?
              AND d.discount_pct >= ?
              AND d.discount_pct < 95
            ORDER BY d.discount_pct DESC, (d.original_price - d.current_price) DESC
            """,
            (cutoff, min_reference, min_discount),
        )
        return [dict(row) for row in rows if row["url"] not in legacy_sent]


def render_digest(deals: list[dict], *, email: str, site_url: str, secret: str) -> tuple[str, str]:
    unsub_qs = urlencode({"email": email, "sig": unsubscribe_signature(email, secret)})
    unsub_url = f"{site_url.rstrip('/')}/alerts/unsubscribe?{unsub_qs}"
    lines = ["FreshPowder Rare-Deal Drop (55%+ Off)", ""]
    items_html = []
    for d in deals:
        sym = "C$" if d["currency"] == "CAD" else "$"
        pct = round(d["discount_pct"])
        sz = f" · Sizes: {d['sizes']}" if d.get("sizes") else ""
        lines.append(f"- {pct}% OFF: {d['name']} — {sym}{d['current_price']:,.0f} (was {sym}{d['original_price']:,.0f}) at {d['store']}{sz}\n  {d['url']}")
        items_html.append(
            f"<li><strong>{pct}% OFF</strong> · <a href=\"{html.escape(d['url'])}\">{html.escape(d['name'])}</a> "
            f"— <strong>{sym}{d['current_price']:,.0f}</strong> <s>{sym}{d['original_price']:,.0f}</s> "
            f"({html.escape(d['store'])}{html.escape(sz)})</li>"
        )
    lines.extend(["", f"Browse all deals: {site_url}", f"Unsubscribe: {unsub_url}"])
    body_html = (
        "<h2>FreshPowder · Standout 55%+ Markdowns</h2>"
        f"<ul>{''.join(items_html)}</ul>"
        f"<p><a href=\"{html.escape(site_url)}\">Browse all deals</a> · "
        f"<a href=\"{html.escape(unsub_url)}\">Unsubscribe</a></p>"
    )
    return "\n".join(lines), body_html


def post_digest(client: httpx.Client, payload: dict, *, removal_hash: str) -> httpx.Response:
    key = sha256(f"{removal_hash}:{json.dumps(payload, sort_keys=True)}".encode()).hexdigest()
    for attempt in range(3):
        response = client.post("https://api.resend.com/emails", headers={"Idempotency-Key": key}, json=payload)
        if response.status_code != 429 or attempt == 2:
            break
        retry_after = response.headers.get("Retry-After") or response.headers.get("RateLimit-Reset")
        try:
            delay = float(retry_after) if retry_after else 2 ** attempt
        except ValueError:
            delay = 2 ** attempt
        if not 0 <= delay <= 60:
            break
        sleep(delay)
    return response


def send_alerts(deals_db: Path, *, api_key: str = "", from_email: str = "", secret: str = "", site_url: str = "https://freshpowder.clifan.com", transport=None) -> dict:
    deals = qualifying_rare_deals(deals_db)
    if not deals:
        return {"status": "no_new_rare_deals", "candidates": 0, "sent": 0}
    if not api_key or not from_email or not secret:
        return {"status": "skipped_unconfigured", "candidates": len(deals), "sent": 0}

    conn = alert_signup_db()
    recipients = conn.execute("SELECT email, removal_hash FROM deal_alert_signups ORDER BY created_at").fetchall()
    if not recipients:
        return {"status": "no_subscribers", "candidates": len(deals), "sent": 0}
    conn.execute("""CREATE TABLE IF NOT EXISTS deal_alert_deliveries (
        removal_hash TEXT NOT NULL, deal_url TEXT NOT NULL, sent_at TEXT NOT NULL,
        PRIMARY KEY (removal_hash, deal_url)
    )""")
    conn.commit()
    _sync()

    sent = 0
    failed = 0
    with httpx.Client(timeout=20, transport=transport, headers={"Authorization": f"Bearer {api_key}"}) as client:
        for email, removal_hash in recipients:
            sent_urls = {row[0] for row in conn.execute(
                "SELECT deal_url FROM deal_alert_deliveries WHERE removal_hash = ?", (removal_hash,)
            ).fetchall()}
            pending = [deal for deal in deals if deal["url"] not in sent_urls][:8]
            if not pending:
                continue
            text_body, html_body = render_digest(pending, email=email, site_url=site_url, secret=secret)
            subject = f"FreshPowder: {len(pending)} standout 55%+ ski & snowboard markdowns"
            payload = {"from": from_email, "to": [email], "subject": subject, "text": text_body, "html": html_body}
            try:
                response = post_digest(client, payload, removal_hash=removal_hash)
            except httpx.TransportError:
                failed += 1
                continue
            if not response.is_success:
                failed += 1
                if response.status_code == 429:
                    break
                continue
            now = datetime.now(UTC).isoformat()
            conn.executemany(
                "INSERT OR IGNORE INTO deal_alert_deliveries (removal_hash, deal_url, sent_at) VALUES (?, ?, ?)",
                [(removal_hash, deal["url"], now) for deal in pending],
            )
            conn.commit()
            _sync()
            sent += 1
    if not sent and not failed:
        return {"status": "no_new_rare_deals", "candidates": 0, "sent": 0}
    status = "send_failed" if not sent else "partial_failure" if failed else "sent"
    return {"status": status, "candidates": len(deals), "subscribers": len(recipients), "sent": sent, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deals-db", type=Path, default=ROOT / "catalogs/fresh-powder/data/deals.db")
    parser.add_argument("--site-url", default=os.environ.get("SITE_URL", "https://freshpowder.clifan.com"))
    args = parser.parse_args()
    summary = send_alerts(
        args.deals_db,
        api_key=os.environ.get("RESEND_API_KEY", ""),
        from_email=os.environ.get("ALERT_FROM_EMAIL", ""),
        secret=os.environ.get("SECRET_KEY", ""),
        site_url=args.site_url,
    )
    print(json.dumps(summary, indent=2))
    return int(summary["status"] == "send_failed")


if __name__ == "__main__":
    raise SystemExit(main())
