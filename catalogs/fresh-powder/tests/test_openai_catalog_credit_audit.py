"""Offline unit tests for scripts/openai_catalog_credit_audit.py."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from aggregator.db import init_db, upsert_deals
from aggregator.models import AggregatedDeal
from deal_common.offers import VariantOffer
from scripts import openai_catalog_credit_audit as audit_cli


def _make_fixture_db(db_path: Path) -> None:
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    deals = [
        AggregatedDeal(
            id=None,
            store="Store Alpha",
            name="Ski Strap - Single",
            url="https://alpha.example/products/ski-strap",
            current_price=4.0,
            original_price=52.0,
            discount_pct=92.3,
            category="accessories",
            sizes=None,
            length_min=None,
            length_max=None,
            scraped_at=now,
            brand="alpha",
            product_type="Accessories",
            offers=[
                VariantOffer("v1", "https://alpha.example/products/ski-strap?v=1", 4.0, 52.0, None, "Black", "in_stock")
            ],
        ),
        AggregatedDeal(
            id=None,
            store="Store Beta",
            name="All-Mountain Snowboard - Final Sale 2026",
            url="https://beta.example/products/board-final-sale",
            current_price=299.0,
            original_price=600.0,
            discount_pct=50.2,
            category="snowboards",
            sizes="154 cm",
            length_min=154,
            length_max=158,
            scraped_at=now,
            brand="burton",
            product_type="Snowboards",
            offers=[
                VariantOffer("v1", "https://beta.example/products/board-final-sale?v=1", 299.0, 600.0, "154 cm", "154 cm", "in_stock"),
                VariantOffer("v2", "https://beta.example/products/board-final-sale?v=2", 480.0, 600.0, "158 cm", "158 cm", "in_stock"),
            ],
        ),
        AggregatedDeal(
            id=None,
            store="Store Gamma",
            name="Roof Rack Tower Pair",
            url="https://gamma.example/products/roof-rack",
            current_price=120.0,
            original_price=240.0,
            discount_pct=50.0,
            category=None,
            sizes=None,
            length_min=None,
            length_max=None,
            scraped_at=now,
            brand="yakima",
            product_type=None,
        ),
    ]
    asyncio.run(init_db(db_path))
    asyncio.run(upsert_deals(deals, db_path))


def test_gateway_guard_rejects_external_hosts():
    assert audit_cli.assert_local_gateway("http://127.0.0.1:18455/v1/") == "http://127.0.0.1:18455/v1"
    assert audit_cli.assert_local_gateway("http://localhost:18455/v1") == "http://localhost:18455/v1"
    with pytest.raises(ValueError, match="non-loopback"):
        audit_cli.assert_local_gateway("https://api.openai.com/v1")


def test_readonly_snapshot_load_and_stratified_sampling(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_cli, "load_supplemental_raw_feeds", lambda: {})
    db_path = tmp_path / "deals.db"
    _make_fixture_db(db_path)
    before_bytes = db_path.read_bytes()

    offers = audit_cli.load_active_offers(db_path)
    assert db_path.read_bytes() == before_bytes
    assert len(offers) == 3

    by_store = {o["store"]: o for o in offers}
    assert by_store["Store Alpha"]["stratum"] == "extreme_discount_or_legacy_msrp"
    assert by_store["Store Beta"]["variant_price_spread"] == 181.0
    assert by_store["Store Beta"]["stratum"] == "condition_or_fulfillment_caveat"
    assert by_store["Store Gamma"]["stratum"] == "unresolved_or_generic_category"

    sampled = audit_cli.select_stratified_sample(offers, limit=3, seed=7)
    assert {o["store"] for o in sampled} == {"Store Alpha", "Store Beta", "Store Gamma"}


def test_batch_line_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_cli, "load_supplemental_raw_feeds", lambda: {})
    db_path = tmp_path / "deals.db"
    _make_fixture_db(db_path)
    offer = audit_cli.load_active_offers(db_path)[0]

    resp_line = audit_cli.build_batch_line(offer, endpoint="/v1/responses", model="gpt-6.1-sol")
    assert resp_line["url"] == "/v1/responses"
    assert resp_line["body"]["model"] == "gpt-6.1-sol"
    assert resp_line["body"]["text"]["format"] == {"type": "json_object"}
    assert "json" in resp_line["body"]["input"][1]["content"].lower()

    chat_line = audit_cli.build_batch_line(offer, endpoint="/v1/chat/completions", model="gpt-6.1-sol")
    assert chat_line["url"] == "/v1/chat/completions"
    assert chat_line["body"]["response_format"] == {"type": "json_object"}
    assert "json" in chat_line["body"]["messages"][1]["content"].lower()


def test_cli_live_and_batch_lifecycle_with_mock_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_cli, "load_supplemental_raw_feeds", lambda: {})
    db_path = tmp_path / "deals.db"
    runtime_dir = tmp_path / "runtime"
    _make_fixture_db(db_path)

    recorded_calls: list[tuple[str, str, str | None]] = []

    def fake_gateway_request(
        gateway_base: str,
        method: str,
        path: str,
        *,
        json_body=None,
        raw_body=None,
        content_type=None,
        account=None,
        timeout=60.0,
    ):
        assert gateway_base == "http://127.0.0.1:18455/v1"
        recorded_calls.append((method, path, account))
        hdrs = {
            "X-Quotaswitch-Account-Id": account or "api-clfgpt3-exp1129",
            "X-Quotaswitch-Provider": "openai-api-key" if account else "codex",
        }
        if method == "POST" and path == "/chat/completions":
            verdict_json = json.dumps(
                {
                    "verdict": "fail",
                    "severity": 3,
                    "flags": ["inflated_reference_price"],
                    "suggested_category": "accessories",
                    "audience": "adult_unisex",
                    "estimated_fair_msrp_range": "$5-$10 USD",
                    "price_plausible": False,
                    "summary": "Inflated single-strap MSRP.",
                }
            )
            return (
                200,
                hdrs,
                json.dumps(
                    {
                        "choices": [{"message": {"content": verdict_json}}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 40},
                    }
                ).encode(),
            )
        if method == "POST" and path == "/files":
            assert raw_body is not None and b'name="purpose"\r\n\r\nbatch' in raw_body
            return 200, hdrs, json.dumps({"id": "file-test123", "object": "file"}).encode()
        if method == "POST" and path == "/batches":
            assert json_body["input_file_id"] == "file-test123"
            assert json_body["completion_window"] == "24h"
            return (
                200,
                hdrs,
                json.dumps(
                    {
                        "id": "batch_test456",
                        "status": "validating",
                        "endpoint": json_body["endpoint"],
                        "input_file_id": "file-test123",
                        "request_counts": {"total": 3, "completed": 0, "failed": 0},
                    }
                ).encode(),
            )
        if method == "GET" and path == "/batches/batch_test456":
            return (
                200,
                hdrs,
                json.dumps(
                    {
                        "id": "batch_test456",
                        "status": "completed",
                        "endpoint": "/v1/responses",
                        "model": "gpt-6.1-sol",
                        "output_file_id": "file-out789",
                        "error_file_id": None,
                        "request_counts": {"total": 3, "completed": 3, "failed": 0},
                    }
                ).encode(),
            )
        if method == "GET" and path == "/files/file-out789/content":
            lines = []
            for oid in (1, 2, 3):
                lines.append(
                    json.dumps(
                        {
                            "custom_id": f"offer-{oid}",
                            "response": {
                                "status_code": 200,
                                "body": {
                                    "output": [
                                        {
                                            "type": "message",
                                            "content": [
                                                {
                                                    "type": "output_text",
                                                    "text": json.dumps(
                                                        {
                                                            "verdict": "warn",
                                                            "severity": 2,
                                                            "flags": ["blem_demo_used_condition"],
                                                            "suggested_category": "snowboards",
                                                            "audience": "adult_unisex",
                                                            "estimated_fair_msrp_range": "$550-$600 USD",
                                                            "price_plausible": True,
                                                            "summary": "Blem condition compared against new MSRP.",
                                                        }
                                                    ),
                                                }
                                            ],
                                        }
                                    ],
                                    "usage": {"input_tokens": 120, "output_tokens": 45},
                                },
                            },
                        }
                    )
                )
            return 200, hdrs, ("\n".join(lines) + "\n").encode()
        raise AssertionError(f"Unexpected call: {method} {path}")

    monkeypatch.setattr(audit_cli, "gateway_request", fake_gateway_request)

    base_flags = ["--db-path", str(db_path), "--runtime-dir", str(runtime_dir)]
    assert audit_cli.main([*base_flags, "live", "--limit", "3", "--workers", "4"]) == 0
    assert audit_cli.main([*base_flags, "batch-submit", "--limit", "3"]) == 0
    assert audit_cli.main([*base_flags, "batch-status"]) == 0
    assert audit_cli.main([*base_flags, "batch-collect"]) == 0

    # Verify same X-Quotaswitch-Account header was used for file upload, batch creation, status, and collect
    batch_calls = [c for c in recorded_calls if c[1] != "/chat/completions"]
    assert batch_calls == [
        ("POST", "/files", "api-clfgpt3-exp1129"),
        ("POST", "/batches", "api-clfgpt3-exp1129"),
        ("GET", "/batches/batch_test456", "api-clfgpt3-exp1129"),
        ("GET", "/batches/batch_test456", "api-clfgpt3-exp1129"),
        ("GET", "/files/file-out789/content", "api-clfgpt3-exp1129"),
    ]

    collected = json.loads((runtime_dir / "batches" / "collected-batch_test456.json").read_text())
    assert collected["offers_succeeded"] == 3
    assert collected["flag_counts"] == {"blem_demo_used_condition": 3}
