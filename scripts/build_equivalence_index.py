"""Build and inspect embedding-backed cross-retailer product equivalence indexes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in ("common", "catalogs/cycling/src", "catalogs/tools/src", "catalogs/camp/src", "catalogs/watersports/src"):
    sys.path.insert(0, str(ROOT / rel))

from camp_deals.catalog import CATALOG as CAMP
from cycling_deals.catalog import CATALOG as CYCLING
from deal_common.equivalence import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDINGS_URL,
    DEFAULT_SIMILARITY_THRESHOLD,
    EmbeddingCache,
    OpenAIEmbeddingClient,
    build_equivalence_index,
    load_deal_common_items,
    load_fresh_powder_items,
    serialize_index_summary,
)
from tool_deals.catalog import CATALOG as TOOLS
from water_deals.catalog import CATALOG as WATER

CATALOGS = {
    "cycling": CYCLING,
    "tools": TOOLS,
    "camp": CAMP,
    "watersports": WATER,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, choices=("fresh-powder", "cycling", "tools", "camp", "watersports"))
    parser.add_argument("--database", type=Path, required=True, help="Path to read-only catalog SQLite snapshot.")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".local" / "embeddings-cache.sqlite",
        help="Local SQLite embedding cache path.",
    )
    parser.add_argument("--base-url", default=DEFAULT_EMBEDDINGS_URL)
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    parser.add_argument("--max-groups", type=int, default=50)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.catalog == "fresh-powder":
        items = load_fresh_powder_items(args.database)
    else:
        cat_def = CATALOGS[args.catalog]
        items = load_deal_common_items(args.database, cat_def.slug, brand_aliases=cat_def.brand_aliases)

    cache = EmbeddingCache(args.cache)
    embedder = OpenAIEmbeddingClient(cache=cache, base_url=args.base_url, model=args.model)
    index = build_equivalence_index(items, embedder=embedder, catalog=args.catalog, similarity_threshold=args.threshold)
    summary = serialize_index_summary(index, max_groups=args.max_groups)
    summary["cache_stats"] = {
        "api_calls": embedder.api_calls,
        "newly_embedded_inputs": embedder.embedded_inputs,
        "total_cached_vectors": cache.count(args.model),
    }
    rendered = json.dumps(summary, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
