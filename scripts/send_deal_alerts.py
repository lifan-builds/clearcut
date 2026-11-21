"""Send rare-deal digest emails to subscribed addresses when Resend is configured."""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx

ROOT = Path(__file__).resolve().parents[1]
for rel in ("common", "catalogs/fresh-powder/src"):
    sys.path.insert(0, str(ROOT / rel))

from aggregator.auth_db import _sync, alert_signup_db
from aggregator.web.signup_routes import unsubscribe_signature


def qualifying_rare_deals(deals_db: Path, *, min_discount: float = 55.0, min_reference: float = 400.0, limit: int = 8) -> list[dict]:
    if not deals_db.is_file():
        return []
    cutoff = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=36)).isoformat()
    with sqlite3.connect(deals_db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE IF NOT EXISTS sent_deal_alerts (url TEXT PRIMARY KEY, sent_at TEXT NOT NULL)")
        rows = conn.execute(
            """
            SELECT d.store, d.name, d.url, d.current_price, d.original_price, d.discount_pct, d.sizes, d.currency
            FROM deals d
            LEFT JOIN sent_deal_alerts s ON s.url = d.url
            WHERE s.url IS NULL
              AND d.exclusion_reason IS NULL
              AND d.scraped_at >= ?
              AND d.category IN ('skis', 'snowboards', 'ski boots', 'snowboard boots', 'bindings')
              AND d.original_price >= ?
              AND d.discount_pct >= ?
              AND d.discount_pct < 95
            ORDER BY d.discount_pct DESC, (d.original_price - d.current_price) DESC
            LIMIT ?
            """,
            (cutoff, min_reference, min_discount, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_deals_sent(deals_db: Path, urls: list[str]) -> None:
    if not urls:
        return
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(deals_db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sent_deal_alerts (url TEXT PRIMARY KEY, sent_at TEXT NOT NULL)")
        conn.executemany("INSERT OR IGNORE INTO sent_deal_alerts VALUES (?, ?)", [(u, now) for u in urls])
        conn.commit()


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
    lines.extend(["", f"Browse all deals: {site_url}", f"Unsubscribe in one click: {unsub_url}"])
    body_html = (
        "<h2>FreshPowder · Standout 55%+ Markdowns</h2>"
        f"<ul>{''.join(items_html)}</ul>"
        f"<p><a href=\"{html.escape(site_url)}\">Browse all deals</a> · "
        f"<a href=\"{html.escape(unsub_url)}\">Unsubscribe</a></p>"
    )
    return "\n".join(lines), body_html


def send_alerts(deals_db: Path, *, api_key: str = "", from_email: str = "", secret: str = "", site_url: str = "https://freshpowder.clifan.com", transport=None) -> dict:
    deals = qualifying_rare_deals(deals_db)
    if not deals:
        return {"status": "no_new_rare_deals", "candidates": 0, "sent": 0}
    if not api_key or not from_email or not secret:
        return {"status": "skipped_unconfigured", "candidates": len(deals), "sent": 0}

    conn = alert_signup_db()
    emails = [row[0] for row in conn.execute("SELECT email FROM deal_alert_signups ORDER BY created_at").fetchall()]
    _sync()
    if not emails:
        return {"status": "no_subscribers", "candidates": len(deals), "sent": 0}

    sent = 0
    subject = f"FreshPowder: {len(deals)} standout 55%+ ski & snowboard markdowns"
    with httpx.Client(timeout=20, transport=transport) as client:
        for recipient in emails:
            text_body, html_body = render_digest(deals, email=recipient, site_url=site_url, secret=secret)
            resp = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"from": from_email, "to": [recipient], "subject": subject, "text": text_body, "html": html_body},
            )
            if resp.status_code < 300:
                sent += 1
    if sent > 0:
        mark_deals_sent(deals_db, [d["url"] for d in deals])
    return {"status": "sent", "candidates": len(deals), "subscribers": len(emails), "sent": sent}


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
