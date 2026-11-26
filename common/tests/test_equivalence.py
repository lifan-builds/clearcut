"""Tests for the embedding-backed cross-retailer product equivalence index."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest
from aggregator.db import init_db, upsert_deals
from aggregator.models import AggregatedDeal
from cycling_deals.catalog import CATALOG as CYCLING
from deal_common.catalog.storage import save_source
from deal_common.equivalence import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDINGS_URL,
    CanonicalItem,
    EmbeddingCache,
    OpenAIEmbeddingClient,
    are_items_compatible,
    build_equivalence_index,
    embedding_text,
    extract_model_year,
    load_deal_common_items,
    load_fresh_powder_items,
    query_similar,
    serialize_index_summary,
)
from tool_deals.catalog import CATALOG as TOOLS


def _deterministic_vec(text: str, dims: int = 32) -> list[float]:
    """Deterministic unit-ish test vector seeded from normalized tokens so related items align."""
    tokens = [tok for tok in text.lower().replace("|", " ").split() if len(tok) > 1]
    acc = [0.0] * dims
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(dims):
            acc[i] += (digest[i % len(digest)] - 127.5) / 127.5
    if not any(acc):
        acc[0] = 1.0
    return acc


class IdenticalFamilyEmbedder:
    """High-similarity embedder (cosine ~0.99 within brand) to stress-test precision guards."""

    model = DEFAULT_EMBEDDING_MODEL

    def embed_texts(self, texts):
        out = []
        for idx, text in enumerate(texts):
            # Nearly identical vectors so cosine similarity is >0.99 for all candidates;
            # structural precision guards MUST still reject every distinct model/year/spec.
            vec = [1.0, 0.001 * ((idx % 7) + 1), 0.5, 0.25]
            norm = sum(v * v for v in vec) ** 0.5
            out.append(tuple(v / norm for v in vec))
        return out


def test_openai_embedding_client_batches_caches_retries_and_preserves_index_order(tmp_path):
    cache = EmbeddingCache(tmp_path / "embeddings.sqlite")
    seen_requests: list[tuple[str, dict, dict]] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        body = json.loads(request.content.decode("utf-8"))
        seen_requests.append((str(request.url), dict(request.headers), body))
        if attempts == 1:
            return httpx.Response(503, json={"error": "temporary upstream capacity"})
        inputs = body["input"]
        # Deliberately return reversed indices to verify strict index sorting
        data = [
            {"index": i, "embedding": _deterministic_vec(inputs[i])}
            for i in reversed(range(len(inputs)))
        ]
        return httpx.Response(200, json={"model": body["model"], "data": data})

    transport = httpx.MockTransport(handler)
    client = OpenAIEmbeddingClient(cache=cache, batch_size=2, transport=transport)
    texts = [
        "Atomic Bent 100 Skis 2026",
        "Yeti SB140 C2 90",
        "Atomic Bent 100 Skis 2026",  # duplicate in same call
        "DeWalt DCS334B Jigsaw",
    ]
    vectors = client.embed_texts(texts)
    assert len(vectors) == 4
    assert vectors[0] == vectors[2]
    assert client.api_calls == 2  # 3 unique texts at batch_size=2 -> 2 batches
    assert client.embedded_inputs == 3
    assert cache.count(DEFAULT_EMBEDDING_MODEL) == 3
    assert seen_requests[0][0] == DEFAULT_EMBEDDINGS_URL
    assert seen_requests[0][1]["x-quotaswitch-auth"] == "api_key"
    assert seen_requests[0][2]["model"] == DEFAULT_EMBEDDING_MODEL
    for vec in vectors:
        assert sum(v * v for v in vec) == pytest.approx(1.0, abs=1e-5)

    # Second run with same texts + 1 new text should only embed the 1 missing text
    client2 = OpenAIEmbeddingClient(cache=cache, batch_size=2, transport=transport)
    vectors_cached = client2.embed_texts(texts[:3])
    assert client2.api_calls == 0
    assert vectors_cached == vectors[:3]

    vectors_mixed = client2.embed_texts([texts[0], "Powermatic PM2000B Table Saw"])
    assert client2.api_calls == 1
    assert client2.embedded_inputs == 1
    assert vectors_mixed[0] == vectors[0]


def test_snow_equivalence_merges_cross_shop_skis_and_blocks_false_merges():
    items = [
        # True cross-retailer trio: Atomic Maverick 96 CTI 2026
        CanonicalItem("fresh-powder", "Evo", "https://evo.test/maverick-96",
                      "Atomic Maverick 96 CTI Skis 2026", brand="atomic", category="skis",
                      current_price=599.99, original_price=849.99),
        CanonicalItem("fresh-powder", "Peter Glenn", "https://pg.test/maverick-96",
                      "Atomic Maverick 96 CTI Ski (Men's) - 2026 WINTER", brand="atomic", category="skis",
                      current_price=579.99, original_price=849.99),
        CanonicalItem("fresh-powder", "Ski Pro", "https://skipro.test/maverick-96",
                      "Maverick 96 CTI Flat 25/26", brand="atomic", category="skis",
                      current_price=549.95, original_price=850.00),
        # Traps that MUST NOT merge into Maverick 96 CTI 2026:
        CanonicalItem("fresh-powder", "Backcountry", "https://bc.test/maverick-96-2025",
                      "Atomic Maverick 96 CTI Skis 2025", brand="atomic", category="skis",
                      current_price=499.99, original_price=849.99),
        CanonicalItem("fresh-powder", "Backcountry", "https://bc.test/maverick-88-2026",
                      "Atomic Maverick 88 TI Skis 2026", brand="atomic", category="skis",
                      current_price=499.99, original_price=799.99),
        CanonicalItem("fresh-powder", "Aspen", "https://aspen.test/maverick-96-sys",
                      "Atomic Maverick 96 CTI (Strive 14 System Binding) Skis 2026", brand="atomic", category="skis",
                      current_price=749.99, original_price=1099.99),
        CanonicalItem("fresh-powder", "Evo", "https://evo.test/maverick-96-womens",
                      "Atomic Maverick 96 CTI Women's Skis 2026", brand="atomic", category="skis",
                      current_price=599.99, original_price=849.99),
        # Boot flex & touring traps
        CanonicalItem("fresh-powder", "Evo", "https://evo.test/hawx-120",
                      "Atomic Hawx Prime 120 S GW Ski Boots 2026", brand="atomic", category="ski boots",
                      current_price=559.99, original_price=799.99),
        CanonicalItem("fresh-powder", "Backcountry", "https://bc.test/hawx-110",
                      "Atomic Hawx Prime 110 S GW Ski Boots 2026", brand="atomic", category="ski boots",
                      current_price=489.99, original_price=699.99),
        CanonicalItem("fresh-powder", "Backcountry", "https://bc.test/hawx-xtd-120",
                      "Atomic Hawx Prime XTD 120 GW Ski Boots 2026", brand="atomic", category="ski boots",
                      current_price=599.99, original_price=899.99),
        # Snowboard wide vs regular trap
        CanonicalItem("fresh-powder", "Evo", "https://evo.test/custom-2026",
                      "Burton Custom Camber Snowboard 2026", brand="burton", category="snowboards",
                      current_price=519.99, original_price=699.95),
        CanonicalItem("fresh-powder", "Backcountry", "https://bc.test/custom-wide-2026",
                      "Burton Custom Camber Wide Snowboard 2026", brand="burton", category="snowboards",
                      current_price=519.99, original_price=699.95),
        # Shared Step-On / BOA technology tokens with different model names MUST NOT merge
        CanonicalItem("fresh-powder", "Evo", "https://evo.test/highshot",
                      "Burton Highshot X Pro Step On Snowboard Boots 2026", brand="burton", category="snowboard boots",
                      current_price=487.0, original_price=729.95),
        CanonicalItem("fresh-powder", "Backcountry", "https://bc.test/waverange",
                      "Burton Waverange X Pro Step On Snowboard Boots 2026", brand="burton", category="snowboard boots",
                      current_price=474.95, original_price=729.95),
        # Gnu Gloss vs Gnu Gloss C vs Gnu Frosting MUST NOT merge
        CanonicalItem("fresh-powder", "UtahSkis", "https://us.test/gloss-c",
                      "Gnu Gloss C Womens Snowboard 2026", brand="gnu", category="snowboards",
                      current_price=269.95, original_price=429.99),
        CanonicalItem("fresh-powder", "Backcountry", "https://bc.test/gloss",
                      "Gnu Gloss Snowboard - 2026 - Women's", brand="gnu", category="snowboards",
                      current_price=365.49, original_price=429.99),
        CanonicalItem("fresh-powder", "Blauer", "https://bl.test/frosting",
                      "GNU Frosting Snowboard 2026 - Women's", brand="gnu", category="snowboards",
                      current_price=287.99, original_price=479.99),
    ]
    index = build_equivalence_index(items, embedder=IdenticalFamilyEmbedder(), catalog="fresh-powder")
    assert len(index.groups) == 1
    group = index.groups[0]
    assert group.store_count == 3
    assert group.year == 2026
    assert {it.store for it in group.items} == {"Evo", "Peter Glenn", "Ski Pro"}
    assert group.prices_by_currency["USD"] == {"low": 549.95, "high": 599.99, "spread": 50.04}


def test_cycling_equivalence_merges_yeti_sb140_c2_90_and_separates_builds_and_tiers():
    items = [
        CanonicalItem("cycling", "Pro Bike Supply", "https://pbs.test/sb140-c2",
                      "SB140 Carbon Series - C2 90 T-Type Build", brand="Yeti", category="Mountain bikes",
                      current_price=5000.0, original_price=6200.0),
        CanonicalItem("cycling", "Worldwide Cyclery", "https://wwc.test/sb140-c2",
                      'Yeti SB140 29" Carbon Series Complete Bike w/ C2 SRAM Series 90 T-Type Build Emerald',
                      brand="Yeti", category="Mountain bikes", current_price=5000.0, original_price=6200.0),
        CanonicalItem("cycling", "Gravity Coalition", "https://gc.test/sb140-c2",
                      "Yeti SB140 C2 90", brand="Yeti", category="Mountain bikes",
                      current_price=4974.97, original_price=6200.0),
        # Distinct builds / models / tiers that MUST NOT merge:
        CanonicalItem("cycling", "Worldwide Cyclery", "https://wwc.test/sb140-c3",
                      'Yeti SB140 29" Carbon Series Complete Bike w/ C3 SRAM GX T-Type AXS Build Turquoise',
                      brand="Yeti", category="Mountain bikes", current_price=5200.0, original_price=6700.0),
        CanonicalItem("cycling", "Pro Bike Supply", "https://pbs.test/sb140-t2",
                      "SB140 Turq Series - T2 X0/90 T-Type Build", brand="Yeti", category="Mountain bikes",
                      current_price=6320.0, original_price=7900.0),
        CanonicalItem("cycling", "Colorado Cyclist", "https://cc.test/sb160-c2",
                      "Yeti SB160 C2 29 - Eagle 90 - Raw Carbon", brand="Yeti", category="Mountain bikes",
                      current_price=5200.0, original_price=6400.0),
        # AXS vs mechanical gravel trap
        CanonicalItem("cycling", "Mike's Bikes", "https://mb.test/aspero-mech",
                      "Cervelo Aspero Apex XPLR 1", brand="Cervelo", category="Gravel bikes",
                      current_price=3374.95, original_price=3749.99),
        CanonicalItem("cycling", "Contender", "https://cb.test/aspero-axs",
                      "Cervelo Aspero Apex XPLR AXS 1", brand="Cervelo", category="Gravel bikes",
                      current_price=4094.95, original_price=4549.99),
        # S-Works vs non-S-Works frameset trap
        CanonicalItem("cycling", "Incycle", "https://incycle.test/sw-stump",
                      "2024 Specialized S-Works Stumpjumper Frameset", brand="Specialized", category="Framesets",
                      current_price=1999.0, original_price=3200.0),
        CanonicalItem("cycling", "Bike Mart", "https://bm.test/stump",
                      "2024 Specialized Stumpjumper Frameset", brand="Specialized", category="Framesets",
                      current_price=1499.0, original_price=2800.0),
    ]
    index = build_equivalence_index(items, embedder=IdenticalFamilyEmbedder(), catalog="cycling")
    assert len(index.groups) == 1
    group = index.groups[0]
    assert {it.store for it in group.items} == {"Pro Bike Supply", "Worldwide Cyclery", "Gravity Coalition"}
    assert group.prices_by_currency["USD"]["low"] == 4974.97


def test_tools_equivalence_merges_same_mpn_bare_tool_and_separates_kits_hp_phase_and_fence():
    items = [
        CanonicalItem("tools", "Tool Nut", "https://toolnut.test/dcs334b",
                      "DeWALT 20V MAX XR Brushless Cordless Top-Handle Jigsaw, Tool Only (DCS334B)",
                      brand="DeWalt", category="Power tools", format="Bare tool", platform="DeWalt 20V MAX",
                      current_price=149.0, original_price=219.0),
        CanonicalItem("tools", "Fasteners Inc", "https://fasteners.test/dcs334b",
                      "DEWALT DCS334B 20V MAX XR Cordless Jig Saw (Tool Only)",
                      brand="DeWalt", category="Power tools", format="Bare tool", platform="DeWalt 20V MAX",
                      current_price=139.0, original_price=219.0),
        # Kit vs Bare tool trap
        CanonicalItem("tools", "Max Warehouse", "https://max.test/dcs334p1",
                      "DEWALT DCS334P1 20V MAX XR Cordless Jigsaw Kit",
                      brand="DeWalt", category="Power tools", format="Kit", platform="DeWalt 20V MAX",
                      current_price=249.0, original_price=329.0),
        # Barrel-grip sibling MPN trap (DCS335B vs DCS334B)
        CanonicalItem("tools", "Factory Direct", "https://fds.test/dcs335b",
                      "DEWALT DCS335B 20V MAX XR Barrel Grip Jigsaw Tool Only",
                      brand="DeWalt", category="Power tools", format="Bare tool", platform="DeWalt 20V MAX",
                      current_price=149.0, original_price=219.0),
        # Stationary machine HP / phase / fence-width traps sharing PM2000B family code
        CanonicalItem("tools", "Woodcraft", "https://wc.test/pm-3hp-30",
                      '10" Table Saw with 30" Rip Capacity - 3 HP 1 Ph 230V - PM2000B',
                      brand="Powermatic", category="Workshop equipment", current_price=3899.0, original_price=4499.0),
        CanonicalItem("tools", "OakTree", "https://ot.test/pm-5hp-30",
                      '10" Table Saw with 30" Rip Capacity - 5 HP 1 Ph 230V - PM2000B',
                      brand="Powermatic", category="Workshop equipment", current_price=4199.0, original_price=4799.0),
        CanonicalItem("tools", "Beaver", "https://bv.test/pm-5hp-3ph",
                      '10" Table Saw with 30" Rip Capacity - 5 HP 3 Ph 230V - PM2000B',
                      brand="Powermatic", category="Workshop equipment", current_price=4199.0, original_price=4799.0),
        CanonicalItem("tools", "Tool Nut", "https://tn.test/pm-3hp-50",
                      '10" Table Saw with 50" Rip Capacity - 3 HP 1 Ph 230V - PM2000B',
                      brand="Powermatic", category="Workshop equipment", current_price=4099.0, original_price=4699.0),
        # SawStop Professional (PCS) vs Industrial (ICS) cabinet saw trap
        CanonicalItem("tools", "Woodcraft", "https://wc.test/pcs-3hp",
                      '3 HP Professional Cabinet Saw with 52" Professional T-Glide Advance Fence System - 1 PH 230V',
                      brand="SawStop", category="Workshop equipment", current_price=3784.0, original_price=4134.0),
        CanonicalItem("tools", "OakTree", "https://ot.test/ics-3hp",
                      'SawStop 3HP, 1ph, 230v Industrial Cabinet Saw w/52" T-Glide Advance Fence, Rails & Extension Table',
                      brand="SawStop", category="Workshop equipment", current_price=4936.0, original_price=5436.0),
        # JET 1640 vs 1840 lathe trap
        CanonicalItem("tools", "Craft Supplies", "https://cs.test/1640",
                      "JET 1640 EVS Lathe",
                      brand="JET", category="Workshop equipment", current_price=2999.0, original_price=3519.0),
        CanonicalItem("tools", "Woodcraft", "https://wc.test/1840",
                      'EVS Woodworking Lathe - 18" x 40" - 2 HP 230V - JWL-1840EVS',
                      brand="JET", category="Workshop equipment", current_price=3349.0, original_price=3959.0),
    ]
    index = build_equivalence_index(items, embedder=IdenticalFamilyEmbedder(), catalog="tools")
    assert len(index.groups) == 1
    group = index.groups[0]
    assert group.mpns == ("DCS334B",)
    assert {it.store for it in group.items} == {"Tool Nut", "Fasteners Inc"}


def test_complete_linkage_prevents_transitive_chaining():
    class BridgeEmbedder:
        model = DEFAULT_EMBEDDING_MODEL

        def embed_texts(self, texts):
            # A=(1, 0), B=(cos 25°, sin 25°), C=(cos 50°, sin 50°)
            # dot(A,B)=0.906 >= 0.85, dot(B,C)=0.906 >= 0.85, but dot(A,C)=0.643 < 0.85!
            import math as m

            angles = [0.0, m.radians(25.0), m.radians(50.0)]
            return [(m.cos(a), m.sin(a)) for a in angles[: len(texts)]]

    items = [
        CanonicalItem("cycling", "Shop A", "https://a.test/1", "Trek Madone SL 6 Gen 8",
                      brand="Trek", category="Road bikes", current_price=4200, original_price=5500),
        CanonicalItem("cycling", "Shop B", "https://b.test/1", "Trek Madone SL 6 Road Bike Gen 8",
                      brand="Trek", category="Road bikes", current_price=4300, original_price=5500),
        CanonicalItem("cycling", "Shop C", "https://c.test/1", "Trek Domane AL 6 Gen 8",
                      brand="Trek", category="Road bikes", current_price=4400, original_price=5500),
    ]
    index = build_equivalence_index(items, embedder=BridgeEmbedder(), similarity_threshold=0.85)
    assert len(index.groups) == 1
    assert index.groups[0].store_count == 2


def test_snapshot_loaders_and_similarity_query(tmp_path):
    now = datetime.now(UTC)
    cycling_db = tmp_path / "cycling.db"
    save_source(
        cycling_db,
        "cycling",
        "Gravity Coalition",
        now.isoformat(),
        [{
            "name": "Yeti SB140 C2 90",
            "url": "https://gravitycoalition.com/products/yeti-sb140-c2-90",
            "brand": "yeti bikes",
            "category": "Mountain bikes",
            "format": "",
            "platform": "",
            "offers": [{
                "variant_key": "1",
                "url": "https://gravitycoalition.com/products/yeti-sb140-c2-90?variant=1",
                "current_price": 4974.97,
                "original_price": 6200.0,
                "size": "L",
                "options": "SKU YETI-SB140-C2 / L",
                "availability": "in_stock",
            }],
        }],
        "complete",
        {},
        brand_aliases=CYCLING.brand_aliases,
    )
    loaded_cycling = load_deal_common_items(cycling_db, "cycling", brand_aliases=CYCLING.brand_aliases, now=now)
    assert len(loaded_cycling) == 1
    assert loaded_cycling[0].brand == "Yeti"
    assert loaded_cycling[0].skus == ("YETI-SB140-C2",)
    assert loaded_cycling[0].sizes == ("L",)

    snow_db = tmp_path / "deals.db"
    asyncio.run(init_db(snow_db))
    asyncio.run(
        upsert_deals(
            [
                AggregatedDeal(
                    None, "Evo", "Atomic Bent 100 Skis 2026", "https://evo.com/products/bent-100-2026",
                    499.99, 699.99, 28.57, "skis", "172cm, 180cm", 172, 180,
                    now.replace(tzinfo=None), brand="atomic",
                ),
                AggregatedDeal(
                    None, "Backcountry", "Atomic Bent 100 Ski - 2026", "https://backcountry.com/bent-100-2026",
                    479.99, 699.99, 31.42, "skis", "180cm", 180, 180,
                    now.replace(tzinfo=None), brand="atomic",
                ),
            ],
            snow_db,
        )
    )
    loaded_snow = load_fresh_powder_items(snow_db)
    assert len(loaded_snow) == 2
    embedder = IdenticalFamilyEmbedder()
    index = build_equivalence_index(loaded_snow, embedder=embedder, catalog="fresh-powder")
    summary = serialize_index_summary(index)
    assert summary["cross_retailer_groups"] == 1
    neighbors = query_similar(index, loaded_snow[0], embedder=embedder, top_k=3, require_compatible=True)
    assert len(neighbors) == 1
    assert {loaded_snow[0].store, neighbors[0]["store"]} == {"Backcountry", "Evo"}
    assert extract_model_year("Moment Wildcat Tour 118 25/26") == 2026
    assert "ModelYear: 2026" in embedding_text(loaded_snow[0])
    assert not are_items_compatible(loaded_snow[0], CanonicalItem(
        "fresh-powder", "Shop", "https://shop.test/x", "Atomic Bent 90 Skis 2026", brand="atomic", category="skis"
    ))
    assert TOOLS.slug == "tools"
