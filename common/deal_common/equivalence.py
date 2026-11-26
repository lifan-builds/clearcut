"""Embedding-backed cross-retailer SKU / product deduplication and equivalence index."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from deal_common.catalog.definition import manufacturer_name

DEFAULT_EMBEDDINGS_URL = "http://127.0.0.1:18455/v1/embeddings"
DEFAULT_GATEWAY_API_KEY = "quotaswitch-local"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
DEFAULT_SIMILARITY_THRESHOLD = 0.86
DEFAULT_MPN_SIMILARITY_THRESHOLD = 0.78

_DOT: Callable[[Sequence[float], Sequence[float]], float] = getattr(
    math, "sumprod", lambda u, v: sum(x * y for x, y in zip(u, v, strict=False))
)

_BRAND_SUFFIX_RE = re.compile(
    r"\s+(?:skis?|snowboards?|snowboarding|boards?|bikes?|bicycles?|cycling|cycles?|"
    r"power\s+tools?|tools?|machinery|industrial|electric\s+tool|usa|co\.?|inc\.?|corp\.?|ltd\.?)$",
    re.IGNORECASE,
)
_STORE_BOILERPLATE_RE = re.compile(
    r"(?:\bnew\s*!+|\[\s*new\s*\]|\bbrand\s+new\b|\bfree\s+shipping\b|\bon\s+sale\b|"
    r"\bclearance\b|\bcloseout\b)",
    re.IGNORECASE,
)
_BRAKE_WIDTH_LIST_RE = re.compile(r"\bwidth\s*:\s*[\d,\s/.-]+\s*mm\b", re.IGNORECASE)
_DIN_RANGE_RE = re.compile(r"\(\s*din\s*[\d.\s–/-]+\)", re.IGNORECASE)
_SPEED_RPM_RE = re.compile(r"\b\d{3,5}\s*(?:rpm|opm|spm|ipm|bpm|cfm)\b", re.IGNORECASE)
_SKI_CM_IN_TITLE_RE = re.compile(r"\b(?:1[3-9]\d|2[01]\d)\s*cm\b", re.IGNORECASE)
_BIKE_CM_SIZE_RE = re.compile(r"(?:-\s*|\b)(?:4[6-9]|5\d|6[0-4])\s*cm\b", re.IGNORECASE)
_WHEEL_TOKEN_RE = re.compile(
    r'\b(?:29|27\.5|26|24|20)\s*(?:"|\'\'|-?in(?:ch)?|er)\b|\b(?:700c|650b)\b',
    re.IGNORECASE,
)
_NON_MPN_TOKENS = frozenset({
    "m12", "m18", "12v", "18v", "20v", "24v", "36v", "40v", "60v", "80v",
    "110v", "115v", "120v", "208v", "220v", "230v", "240v", "460v", "480v", "575v", "600v",
    "1ph", "3ph", "1hp", "2hp", "3hp", "5hp", "7hp",
    "700c", "650b", "29er", "2wd", "4wd", "1x", "2x", "3x",
    "105", "360", "6061", "4130", "7005", "304",
    "c1", "c2", "c3", "t1", "t2", "t3", "t4",
    "s1", "s2", "s3", "s4", "s5", "s6",
    "r1", "r2", "r3", "x0", "x01", "xx1", "gx", "nx", "sx", "di2",
})
_GENERIC_MODEL_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "new", "old", "edition", "series", "build", "complete",
    "mens", "men", "womens", "women", "wmns", "ladies", "adult", "adults", "unisex", "winter",
    "ski", "skis", "snowboard", "snowboards", "boot", "boots", "binding", "bindings",
    "helmet", "helmets", "goggle", "goggles", "alpine", "freeride", "all", "mountain",
    "backcountry", "carving", "powder", "park", "flat", "open", "pair", "gw", "gripwalk",
    "bike", "bikes", "bicycle", "bicycles", "road", "gravel", "mtb", "trail", "enduro",
    "downhill", "xc", "cross", "country", "electric", "ebike", "hybrid", "fitness", "bmx",
    "dirt", "jumper", "full", "suspension", "frame", "frameset", "wheelset", "wheels",
    "disc", "carbon", "t", "type", "sram", "shimano",
    "black", "white", "red", "blue", "green", "grey", "gray", "navy", "orange", "yellow",
    "purple", "pink", "brown", "teal", "coral", "olive", "tan", "sand", "bone", "charcoal",
    "graphite", "silver", "gold", "bronze", "copper", "turquoise", "emerald", "stone",
    "anthracite", "matte", "gloss", "satin", "metallic", "raw", "solid",
    "cordless", "corded", "brushless", "brushed", "lithium", "ion", "max", "tool", "only",
    "bare", "kit", "inch", "in", "mm", "cm", "amp", "amps", "volt", "volts", "hp", "ph",
    "variable", "speed", "system", "fence", "rails", "extension", "table", "rip", "capacity",
    "step", "on", "boa", "lace", "sj", "tls", "pro", "wide", "amid", "visor", "hd", "photo",
    "camber", "c2x", "btx", "multiple", "color", "options",
})
_PRESENCE_FLAGS_SNOW = (
    ("womens", re.compile(r"\b(?:women'?s?|wmns|ladies|girl'?s?)\b", re.IGNORECASE)),
    ("wide", re.compile(r"\b(?:wide|1[4-7]\dw)\b", re.IGNORECASE)),
    ("system_binding", re.compile(
        r"\b(?:with|w/|incl(?:uding|\.)?)\s+[a-z0-9 -]{0,28}bindings?\b|"
        r"\bsystem\s+(?:skis?|bindings?)\b|\([^)]*\bbindings?\b[^)]*\)|\+\s*[a-z0-9-]+\s+bindings?\b",
        re.IGNORECASE,
    )),
    ("xtd", re.compile(r"\bxtd\b", re.IGNORECASE)),
    ("boa", re.compile(r"\bboa\b", re.IGNORECASE)),
    ("dual_boa", re.compile(r"\bdual\s+boa\b", re.IGNORECASE)),
    ("mips", re.compile(r"\bmips\b", re.IGNORECASE)),
    ("split", re.compile(r"\b(?:splitboard|split)\b", re.IGNORECASE)),
    ("tour", re.compile(r"\b(?:tour|touring|backland|zero\s*g)\b", re.IGNORECASE)),
    ("flying_v", re.compile(r"\bflying[- ]v\b", re.IGNORECASE)),
    ("c2x", re.compile(r"\bc2x\b", re.IGNORECASE)),
    ("btx", re.compile(r"\bbtx\b", re.IGNORECASE)),
    ("step_on", re.compile(r"\bstep[- ]on\b", re.IGNORECASE)),
    ("s_lab", re.compile(r"\bs\s*/?\s*lab\b", re.IGNORECASE)),
    ("carbon", re.compile(r"\bcarbon\b", re.IGNORECASE)),
    ("black_edition", re.compile(r"\bblack\s+edition\b", re.IGNORECASE)),
    ("cti", re.compile(r"\bcti\b", re.IGNORECASE)),
    ("ti", re.compile(r"\bti\b", re.IGNORECASE)),
    ("pro", re.compile(r"\bpro\b", re.IGNORECASE)),
    ("team", re.compile(r"\bteam\b", re.IGNORECASE)),
    ("ultra", re.compile(r"\bultra\b", re.IGNORECASE)),
    ("photo", re.compile(r"\bphoto(?:chromic)?\b", re.IGNORECASE)),
    ("hv", re.compile(r"\bhv\b", re.IGNORECASE)),
    ("mv", re.compile(r"\bmv\b", re.IGNORECASE)),
    ("lv", re.compile(r"\blv\b", re.IGNORECASE)),
)
_PRESENCE_FLAGS_CYCLING = (
    ("s_works", re.compile(r"\bs[- ]works\b", re.IGNORECASE)),
    ("lab71", re.compile(r"\blab\s*71\b", re.IGNORECASE)),
    ("sl", re.compile(r"\bsl\b", re.IGNORECASE)),
    ("slr", re.compile(r"\bslr\b", re.IGNORECASE)),
    ("evo", re.compile(r"\bevo\b", re.IGNORECASE)),
    ("lt", re.compile(r"\blt\b", re.IGNORECASE)),
    ("lr", re.compile(r"\blr\b|\blunch\s+ride\b", re.IGNORECASE)),
    ("ht", re.compile(r"\b(?:ht|hardtail)\b", re.IGNORECASE)),
    ("sport", re.compile(r"\bsport\b", re.IGNORECASE)),
    ("comp", re.compile(r"\bcomp\b", re.IGNORECASE)),
    ("expert", re.compile(r"\bexpert\b", re.IGNORECASE)),
    ("pro_trim", re.compile(r"\bpro\b", re.IGNORECASE)),
    ("elite", re.compile(r"\belite\b(?!\s+fox)", re.IGNORECASE)),
    ("ultimate", re.compile(r"\bultimate\b", re.IGNORECASE)),
    ("team", re.compile(r"\bteam\b", re.IGNORECASE)),
    ("ltd", re.compile(r"\bltd\b|\blimited\b", re.IGNORECASE)),
    ("igh", re.compile(r"\bigh\b", re.IGNORECASE)),
    ("active", re.compile(r"\bactive\b", re.IGNORECASE)),
    ("x_trim", re.compile(r"\b\d+\s+x\b", re.IGNORECASE)),
    ("axs", re.compile(r"\baxs\b|\betap\b", re.IGNORECASE)),
    ("di2", re.compile(r"\bdi2\b", re.IGNORECASE)),
    ("xplr", re.compile(r"\bxplr\b", re.IGNORECASE)),
    ("reserve", re.compile(r"\b(?:reserve|rsv)\b", re.IGNORECASE)),
    ("step_through", re.compile(r"\b(?:step[- ]?(?:through|thru)|low[- ]?(?:entry|step))\b", re.IGNORECASE)),
    ("womens", re.compile(r"\b(?:women'?s?|wmns|ladies)\b", re.IGNORECASE)),
)
_BIKE_FRAME_TIERS = (
    ("turq", re.compile(r"\b(?:turq|t[- ]series|t[1-4])\b", re.IGNORECASE)),
    ("cc", re.compile(r"\bcc\b", re.IGNORECASE)),
    ("c_tier", re.compile(
        r"\b(?:c[1-3]|carbon\s+series|carbon\s+c)\b|(?<=\s)c(?=\s+(?:r|s|gx|x0|xx|xt|70|90|mx|29|27)\b)",
        re.IGNORECASE,
    )),
    ("alloy", re.compile(r"\b(?:al|alloy|aluminum|aluminium|e5|aluxx|smartform)\b", re.IGNORECASE)),
    ("omx", re.compile(r"\bomx\b", re.IGNORECASE)),
    ("omr", re.compile(r"\bomr\b", re.IGNORECASE)),
)
_BIKE_DRIVETRAINS = (
    ("xx_sl", re.compile(r"\bxx\s*sl\b", re.IGNORECASE)),
    ("xx", re.compile(r"\bxx1?\b", re.IGNORECASE)),
    ("x0", re.compile(r"\bx01?\b", re.IGNORECASE)),
    ("gx", re.compile(r"\bgx\b", re.IGNORECASE)),
    ("nx", re.compile(r"\bnx\b", re.IGNORECASE)),
    ("sx", re.compile(r"\bsx\b", re.IGNORECASE)),
    ("eagle_90", re.compile(r"\b(?:eagle\s+90|series\s+90|sram\s+90|90\s+kit|(?:c[1-3]|t[1-4]|mx|c)\s+90|90\s+t[- ]type|90\s+mx)\b", re.IGNORECASE)),
    ("eagle_70", re.compile(r"\b(?:eagle\s+70|series\s+70|sram\s+70|70\s+kit|(?:c[1-3]|t[1-4]|mx|c)\s+70|70\s+t[- ]type|70\s+mx)\b", re.IGNORECASE)),
    ("dura_ace", re.compile(r"\bdura[- ]ace\b", re.IGNORECASE)),
    ("ultegra", re.compile(r"\bultegra\b", re.IGNORECASE)),
    ("105", re.compile(r"\bshimano\s+105\b|\b105\s+(?:di2|r7\d{3}|mech)\b", re.IGNORECASE)),
    ("tiagra", re.compile(r"\btiagra\b", re.IGNORECASE)),
    ("cues", re.compile(r"\bcues\b", re.IGNORECASE)),
    ("grx_825", re.compile(r"\b(?:grx\s*)?rx\s*825\b", re.IGNORECASE)),
    ("grx_820", re.compile(r"\b(?:grx\s*)?rx\s*820\b", re.IGNORECASE)),
    ("grx_810", re.compile(r"\b(?:grx\s*)?rx\s*810\b", re.IGNORECASE)),
    ("grx_610", re.compile(r"\b(?:grx\s*)?rx\s*610\b", re.IGNORECASE)),
    ("grx_600", re.compile(r"\b(?:grx\s*)?rx\s*600\b", re.IGNORECASE)),
    ("grx_400", re.compile(r"\b(?:grx\s*)?rx\s*400\b", re.IGNORECASE)),
    ("sram_red", re.compile(r"\bsram\s+red\b|\bred\s+(?:axs|etap|xplr|1\b)", re.IGNORECASE)),
    ("force", re.compile(r"\bforce\b", re.IGNORECASE)),
    ("rival", re.compile(r"\brival\b", re.IGNORECASE)),
    ("apex", re.compile(r"\bapex\b", re.IGNORECASE)),
    ("xtr", re.compile(r"\bxtr\b", re.IGNORECASE)),
    ("xt", re.compile(r"\bxt\b", re.IGNORECASE)),
    ("slx", re.compile(r"\bslx\b", re.IGNORECASE)),
    ("deore", re.compile(r"\bdeore\b", re.IGNORECASE)),
)
_TOOL_KINDS = (
    ("table_saw", re.compile(r"\b(?:table|cabinet|contractor|jobsite)\s+saws?\b", re.IGNORECASE)),
    ("miter_saw", re.compile(r"\bmiter\s+saws?\b", re.IGNORECASE)),
    ("band_saw", re.compile(r"\b(?:band\s*saws?)\b", re.IGNORECASE)),
    ("track_saw", re.compile(r"\b(?:track|plunge)\s+saws?\b", re.IGNORECASE)),
    ("recip_saw", re.compile(r"\b(?:recip(?:rocating)?|sawzall)\s+saws?\b|\bsawzall\b", re.IGNORECASE)),
    ("jigsaw", re.compile(r"\bjig\s*saws?\b", re.IGNORECASE)),
    ("scroll_saw", re.compile(r"\bscroll\s+saws?\b", re.IGNORECASE)),
    ("circular_saw", re.compile(r"\bcircular\s+saws?\b", re.IGNORECASE)),
    ("jointer", re.compile(r"\bjointers?\b", re.IGNORECASE)),
    ("planer", re.compile(r"\bplaners?\b", re.IGNORECASE)),
    ("lathe", re.compile(r"\blathes?\b", re.IGNORECASE)),
    ("drill_press", re.compile(r"\bdrill\s+press(?:es)?\b", re.IGNORECASE)),
    ("dust_collector", re.compile(r"\bdust\s+(?:collectors?|extractors?|processors?)\b", re.IGNORECASE)),
    ("welder", re.compile(r"\bwelders?\b|\bwelding\s+machines?\b", re.IGNORECASE)),
    ("plasma_cutter", re.compile(r"\bplasma\s+cutters?\b", re.IGNORECASE)),
    ("grinder", re.compile(r"\bgrinders?\b", re.IGNORECASE)),
    ("sander", re.compile(r"\bsanders?\b", re.IGNORECASE)),
    ("router", re.compile(r"\brouters?\b", re.IGNORECASE)),
    ("impact_wrench", re.compile(r"\bimpact\s+wrench(?:es)?\b", re.IGNORECASE)),
    ("impact_driver", re.compile(r"\bimpact\s+drivers?\b", re.IGNORECASE)),
    ("drill", re.compile(r"\b(?:driver[- ]drills?|hammer\s+drills?|drills?)\b", re.IGNORECASE)),
    ("nailer", re.compile(r"\b(?:nailers?|staplers?)\b", re.IGNORECASE)),
    ("oscillating", re.compile(r"\boscillating\b", re.IGNORECASE)),
    ("polisher", re.compile(r"\bpolishers?\b", re.IGNORECASE)),
    ("vacuum", re.compile(r"\b(?:vacuums?|vacs?)\b", re.IGNORECASE)),
)


class Embedder(Protocol):
    model: str

    def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...


@dataclass(frozen=True)
class CanonicalItem:
    catalog: str
    store: str
    url: str
    name: str
    brand: str = ""
    category: str = ""
    format: str = ""
    platform: str = ""
    currency: str = "USD"
    current_price: float = 0.0
    original_price: float | None = None
    sizes: tuple[str, ...] = ()
    skus: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductSignature:
    catalog: str
    equipment_class: str
    brand_key: str
    year: int | None
    mpns: tuple[str, ...]
    model_tokens: tuple[str, ...]
    core_numbers: tuple[str, ...]
    presence_flags: frozenset[str]
    frame_tiers: frozenset[str]
    drivetrains: frozenset[str]
    wheel_config: str
    package_format: str
    tool_kind: str
    power_mode: str
    voltage_class: str
    horsepower: str
    phase: str
    cutterhead: str
    fence_inches: str
    primary_inches: tuple[str, ...]


@dataclass(frozen=True)
class EquivalenceGroup:
    group_id: str
    catalog: str
    brand: str
    canonical_name: str
    category: str
    year: int | None
    mpns: tuple[str, ...]
    store_count: int
    listing_count: int
    mean_similarity: float
    min_similarity: float
    prices_by_currency: dict[str, dict[str, float]]
    items: tuple[CanonicalItem, ...]


@dataclass
class EquivalenceIndex:
    model: str
    catalog: str
    items: list[CanonicalItem]
    signatures: list[ProductSignature]
    vectors: list[tuple[float, ...]]
    groups: list[EquivalenceGroup]
    url_to_group: dict[str, EquivalenceGroup] = field(default_factory=dict)


def _l2_normalize(values: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(v * v for v in values))
    if not norm or not math.isfinite(norm):
        raise ValueError("Cannot normalize zero or non-finite embedding vector")
    dims = len(values)
    scaled = [v / norm for v in values]
    return struct.unpack(f"<{dims}f", struct.pack(f"<{dims}f", *scaled))


class EmbeddingCache:
    """Deterministic SQLite float32 cache keyed by (model, sha256(text))."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    model       TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL,
                    dims        INTEGER NOT NULL,
                    vector      BLOB NOT NULL,
                    created_at  TEXT NOT NULL,
                    PRIMARY KEY (model, text_sha256)
                )
                """
            )

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get_many(self, model: str, texts: Sequence[str]) -> dict[str, tuple[float, ...]]:
        if not texts:
            return {}
        digests = list({self.digest(t) for t in texts})
        found: dict[str, tuple[float, ...]] = {}
        with sqlite3.connect(self.path) as conn:
            for start in range(0, len(digests), 500):
                chunk = digests[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT text_sha256, dims, vector FROM embeddings "
                    f"WHERE model = ? AND text_sha256 IN ({placeholders})",
                    (model, *chunk),
                ).fetchall()
                for sha, dims, blob in rows:
                    found[sha] = struct.unpack(f"<{dims}f", blob)
        return found

    def put_many(self, model: str, entries: Iterable[tuple[str, Sequence[float]]]) -> int:
        now = datetime.now(UTC).isoformat()
        rows = []
        for text, vec in entries:
            normed = _l2_normalize(vec)
            dims = len(normed)
            rows.append((model, self.digest(text), dims, struct.pack(f"<{dims}f", *normed), now))
        if not rows:
            return 0
        with sqlite3.connect(self.path) as conn, conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (model, text_sha256, dims, vector, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def count(self, model: str | None = None) -> int:
        with sqlite3.connect(self.path) as conn:
            if model:
                return conn.execute("SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]


class OpenAIEmbeddingClient:
    """OpenAI-compatible `/v1/embeddings` client with local SQLite caching."""

    def __init__(
        self,
        *,
        cache: EmbeddingCache | None = None,
        base_url: str | None = None,
        model: str = DEFAULT_EMBEDDING_MODEL,
        api_key: str | None = None,
        quotaswitch_auth: str = "api_key",
        batch_size: int = 128,
        timeout: float = 90.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.cache = cache
        self.url = base_url or os.environ.get("CLEARCUT_EMBEDDINGS_URL", DEFAULT_EMBEDDINGS_URL)
        self.model = model
        self.api_key = (
            api_key
            if api_key is not None
            else (os.environ.get("QUOTASWITCH_API_KEY") or os.environ.get("OPENAI_API_KEY") or DEFAULT_GATEWAY_API_KEY)
        )
        self.quotaswitch_auth = quotaswitch_auth
        self.batch_size = max(1, batch_size)
        self.timeout = timeout
        self.transport = transport
        self.api_calls = 0
        self.embedded_inputs = 0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.quotaswitch_auth:
            headers["X-Quotaswitch-Auth"] = self.quotaswitch_auth
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        unique_texts = list(dict.fromkeys(texts))
        cached = self.cache.get_many(self.model, unique_texts) if self.cache else {}
        missing = [t for t in unique_texts if EmbeddingCache.digest(t) not in cached]

        if missing:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                for start in range(0, len(missing), self.batch_size):
                    batch = missing[start : start + self.batch_size]
                    payload = {"model": self.model, "input": batch}
                    last_exc: Exception | None = None
                    for attempt in range(3):
                        try:
                            resp = client.post(self.url, headers=self._headers(), json=payload)
                            if resp.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                                time.sleep(0.5 * (2**attempt))
                                continue
                            resp.raise_for_status()
                            body = resp.json()
                            break
                        except httpx.HTTPError as exc:
                            last_exc = exc
                            if attempt < 2:
                                time.sleep(0.5 * (2**attempt))
                                continue
                            raise
                    else:
                        if last_exc:
                            raise last_exc
                        raise RuntimeError("Embedding request failed")

                    self.api_calls += 1
                    self.embedded_inputs += len(batch)
                    data = body.get("data")
                    if not isinstance(data, list) or len(data) != len(batch):
                        raise ValueError("Malformed embeddings response length")
                    ordered = sorted(data, key=lambda row: row["index"])
                    batch_pairs = []
                    for text, row in zip(batch, ordered, strict=True):
                        normed = _l2_normalize(row["embedding"])
                        cached[EmbeddingCache.digest(text)] = normed
                        batch_pairs.append((text, normed))
                    if self.cache:
                        self.cache.put_many(self.model, batch_pairs)

        return [cached[EmbeddingCache.digest(t)] for t in texts]


def normalize_brand_key(brand: str | None) -> str:
    if not brand:
        return ""
    cleaned = unicodedata.normalize("NFKC", brand).strip()
    if not cleaned or cleaned.casefold() == "unspecified":
        return ""
    cleaned = cleaned.replace("é", "e").replace("É", "e").replace("ö", "o").replace("Ö", "o")
    cleaned = _BRAND_SUFFIX_RE.sub("", cleaned).strip().casefold()
    aliases = {
        "black crow": "black crows",
        "blackcrows": "black crows",
        "dwd": "dinosaurs will die",
        "voelkl": "volkl",
        "metabo-hpt": "metabo hpt",
        "hitachi/metabo hpt": "metabo hpt",
        "state bicycle": "state",
        "state": "state",
    }
    cleaned = aliases.get(cleaned, cleaned)
    return re.sub(r"[^a-z0-9]+", " ", cleaned).strip()


def extract_model_year(name: str, url: str = "") -> int | None:
    season_4_2 = re.findall(r"\b(20\d{2})\s*[-–/]\s*(\d{2,4})\b(?![\d.])", name)
    if season_4_2:
        start_s, end_s = season_4_2[-1]
        start = int(start_s)
        end = int(end_s) % 100
        if (start % 100) + 1 == end:
            return (start // 100) * 100 + end
    years = [int(y) for y in re.findall(r"\b(20(?:1[89]|2\d))\b", name)]
    if years:
        return years[-1]
    for start_s, end_s in reversed(
        re.findall(
            r"(?<![\d.])\b(1[89]|2\d)\s*[-–/]\s*(\d{2})\b(?![\d.])(?!\s*(?:mm|cm|m\b|in\b|\"|l\b|oz\b|din\b|ah\b|v\b))",
            name,
            re.IGNORECASE,
        )
    ):
        if int(start_s) + 1 == int(end_s):
            return 2000 + int(end_s)
    apostrophe = re.findall(r"(?<![A-Za-z0-9])['’](2\d)\b(?![\d.\"’'])", name)
    if apostrophe:
        return 2000 + int(apostrophe[-1])
    if url:
        slug = urlsplit(url).path.rsplit("/", 1)[-1]
        slug_years = [int(y) for y in re.findall(r"(?:^|[-_])(202\d)(?=[-_]|$)", slug)]
        if slug_years:
            return slug_years[0]
    return None


def _normalize_fraction_number(raw: str) -> str:
    raw = raw.strip().replace("–", "-")
    whole_frac = re.fullmatch(r"(\d+)\s*-\s*(\d+)/(\d+)", raw)
    if whole_frac and int(whole_frac[3]) != 0:
        val = int(whole_frac[1]) + int(whole_frac[2]) / int(whole_frac[3])
        return f"{val:g}"
    frac = re.fullmatch(r"(\d+)/(\d+)", raw)
    if frac and int(frac[2]) != 0:
        return f"{int(frac[1]) / int(frac[2]):g}"
    try:
        val = float(raw)
        return f"{val:g}"
    except ValueError:
        return raw.casefold()


def extract_mpns(item: CanonicalItem, brand_key: str = "") -> tuple[str, ...]:
    """Extract manufacturer model codes / MPNs from tool & equipment titles and variant SKUs."""
    found: list[str] = []
    text = unicodedata.normalize("NFKC", item.name)
    # Milwaukee NNNN-NN style codes (e.g. 2722-20, 2804-22)
    for m in re.findall(r"\b(\d{4}-\d{2}[A-Z]?)\b", text, re.IGNORECASE):
        code = m.upper()
        if code not in found:
            found.append(code)
    if item.catalog == "tools":
        # Hyphenated numeric MPNs (e.g. Thermal Dynamics 1-1330-1, 1-1731-1)
        for hyp_mpn in re.findall(r"\b(\d-\d{4}-\d{1,2})\b", text):
            if hyp_mpn not in found:
                found.append(hyp_mpn)
        # 6-to-10 digit pure numeric manufacturer part numbers (e.g. Miller 907964, 951000099)
        for num_mpn in re.findall(r"\b(\d{6,10})\b", text):
            if num_mpn not in found:
                found.append(num_mpn)
        for token in re.findall(
            r"\b([A-Za-z]{1,5}\d{2,5}[A-Za-z0-9-]{0,10}|\d{2,5}[A-Za-z]{1,3}(?:-[A-Za-z0-9]{1,10})?)\b",
            text,
        ):
            cleaned_tok = re.sub(r"-(?:POWERMATIC|JET|SAWSTOP|DEWALT|MILWAUKEE|MAKITA)$", "", token, flags=re.IGNORECASE)
            low = cleaned_tok.casefold()
            if low in _NON_MPN_TOKENS or (brand_key and low == brand_key):
                continue
            if re.fullmatch(r"\d+(?:mm|cm|in|ft|lb|oz|ga|hp|ph|ah|rpm|opm|cfm|psi|amp|amps|volt|volts|v)", low):
                continue
            if re.fullmatch(r"20\d{2}", low):
                continue
            code = cleaned_tok.upper()
            if code not in found:
                found.append(code)
    for sku in item.skus:
        cleaned = sku.strip().upper()
        if cleaned and len(cleaned) >= 4 and cleaned not in found:
            found.append(cleaned)
    return tuple(found)


def _equipment_class(catalog: str, category: str) -> str:
    cat = (category or "").strip().casefold()
    if catalog == "cycling":
        if cat in {"framesets", "wheelsets", "groupsets"}:
            return cat
        return "complete_bikes"
    if catalog == "tools":
        return "combo_kits" if cat == "combo kits" else "tools_and_machines"
    return cat or "general"


def _package_format(item: CanonicalItem) -> str:
    fmt = (item.format or "").strip().casefold()
    title = item.name.casefold()
    if fmt in {"bare tool", "kit", "bundle"}:
        return fmt
    if re.search(r"\b(?:bare\s+tool|tool[- ]only)\b", title):
        return "bare tool"
    if re.search(r"\bbundle\b", title):
        return "bundle"
    if re.search(r"\bkit\b", title):
        return "kit"
    return ""


def _clean_title_for_numbers(item: CanonicalItem, brand_key: str) -> str:
    text = unicodedata.normalize("NFKC", item.name)
    text = _BRAKE_WIDTH_LIST_RE.sub(" ", text)
    text = _DIN_RANGE_RE.sub(" ", text)
    text = _SPEED_RPM_RE.sub(" ", text)
    text = _SKI_CM_IN_TITLE_RE.sub(" ", text)
    text = _BIKE_CM_SIZE_RE.sub(" ", text)
    text = _WHEEL_TOKEN_RE.sub(" ", text)
    text = _STORE_BOILERPLATE_RE.sub(" ", text)
    text = re.sub(r"\b20(?:1[89]|2\d)(?:\s*[-–/]\s*\d{2,4})?\b", " ", text)
    text = re.sub(r"(?<![\d.])\b(?:1[89]|2\d)\s*[-–/]\s*\d{2}\b(?![\d.])", " ", text)
    text = re.sub(r"(?<![A-Za-z0-9])['’]2\d\b", " ", text)
    if brand_key == "kona":
        text = re.sub(r"\b3[5-9]e\b", " ", text, flags=re.IGNORECASE)
    if brand_key:
        text = re.sub(r"\b" + re.escape(brand_key) + r"\b", " ", text, flags=re.IGNORECASE)
    return text


def _extract_model_tokens(cleaned_title: str, brand_key: str, catalog: str) -> tuple[str, ...]:
    text = cleaned_title.casefold()
    text = (
        text.replace("wi.de.", "wide")
        .replace("s/pro", "spro")
        .replace("s/lab", "slab")
        .replace("d.o.a.", "doa")
        .replace("a-frame", "aframe")
    )
    if brand_key == "atomic":
        # Atomic retailers alternate between "Bent 100" and "Bent Chetler 100"
        text = re.sub(r"\bbent\s+chetler\b", "bent", text)
    brand_words = set(brand_key.split())
    stopwords = _GENERIC_MODEL_STOPWORDS - ({"gloss"} if brand_key == "gnu" else set())
    tokens: list[str] = []
    for tok in re.findall(r"[a-z][a-z0-9]{1,18}", text):
        if tok in brand_words or tok in stopwords:
            continue
        if catalog == "cycling" and tok in {
            "comp", "sport", "expert", "pro", "elite", "ultimate", "axs", "di2", "xplr",
            "gx", "xx", "nx", "sx", "xt", "xtr", "slx", "deore", "ultegra", "tiagra", "rival", "force", "apex",
            "fox", "float", "factory", "performance", "genie", "exclusive",
        }:
            continue
        if tok not in tokens:
            tokens.append(tok)
    return tuple(tokens[:5])


def extract_product_signature(item: CanonicalItem, *, known_brands: set[str] | None = None) -> ProductSignature:
    brand_key = normalize_brand_key(item.brand)
    if not brand_key and known_brands:
        lower_name = normalize_brand_key(item.name)
        for candidate in sorted(known_brands, key=len, reverse=True):
            if candidate and (lower_name == candidate or lower_name.startswith(candidate + " ")):
                brand_key = candidate
                break

    year = extract_model_year(item.name, item.url)
    mpns = extract_mpns(item, brand_key)
    eq_class = _equipment_class(item.catalog, item.category)
    title = unicodedata.normalize("NFKC", item.name)
    lower = title.casefold()
    cleaned = _clean_title_for_numbers(item, brand_key)
    model_tokens = _extract_model_tokens(cleaned, brand_key, item.catalog)

    flags: set[str] = set()
    frame_tiers: set[str] = set()
    drivetrains: set[str] = set()
    wheel_config = ""
    tool_kind = ""
    power_mode = ""
    voltage_class = ""
    horsepower = ""
    phase = ""
    cutterhead = ""
    fence_inches = ""
    primary_inches: tuple[str, ...] = ()
    core_numbers: tuple[str, ...] = ()

    if item.catalog in {"fresh-powder", "snow"}:
        for name, pattern in _PRESENCE_FLAGS_SNOW:
            if pattern.search(title):
                flags.add(name)
        if re.search(r"\b\d{2,3}\s+w\b(?!\s*/)|\bw\b(?=\s+(?:flat|skis?|snowboards?|boots?|20\d{2}|\d{2}/\d{2})|$)", lower):
            flags.add("womens")
        if re.search(r"\bgloss\s+c\b", lower) and brand_key == "gnu":
            flags.add("gnu_c")
        if "cti" in flags:
            flags.discard("ti")
        nums = re.findall(r"\b\d+(?:\.\d+)?\b", cleaned)
        core_numbers = tuple(dict.fromkeys(_normalize_fraction_number(n) for n in nums))

    elif item.catalog == "cycling":
        for name, pattern in _PRESENCE_FLAGS_CYCLING:
            if pattern.search(title):
                flags.add(name)
        if "slr" in flags:
            flags.discard("sl")
        for name, pattern in _BIKE_FRAME_TIERS:
            if pattern.search(title):
                frame_tiers.add(name)
        if "cc" in frame_tiers:
            frame_tiers.discard("c_tier")
        for name, pattern in _BIKE_DRIVETRAINS:
            if pattern.search(title):
                drivetrains.add(name)
        if "xx_sl" in drivetrains:
            drivetrains.discard("xx")
        if re.search(r"\b(?:mx|mullet)\b", lower):
            wheel_config = "mx"
        elif re.search(r"\b27\.5\b|\b650b\b", lower):
            wheel_config = "27.5"
        elif re.search(r'\b29(?:"|\'\'|-?in(?:ch)?|er|\b)', lower):
            wheel_config = "29"

        tokens = []
        for tok in re.findall(r"\b(?:[a-z]{1,4}\d{1,4}[a-z]{0,3}|\d+(?:\.\d+)?)\b", cleaned.casefold()):
            if tok in {"1x", "2x", "700c", "650b", "29er", "29", "27.5", "6061", "12r", "10r", "9r"}:
                continue
            tokens.append(_normalize_fraction_number(tok) if tok[0].isdigit() else tok)
        core_numbers = tuple(dict.fromkeys(tokens))

    elif item.catalog == "tools":
        for kind_name, pattern in _TOOL_KINDS:
            if pattern.search(title):
                tool_kind = kind_name
                break

        if re.search(r"\bcordless\b|\b(?:m12|m18|20v|18v|12v|24v|36v|40v|60v|flexvolt|lxt|xgt)\b", lower):
            power_mode = "cordless"
        elif re.search(r"\bcorded\b|\b\d+(?:\.\d+)?\s*-?\s*amps?\b", lower):
            power_mode = "corded"

        if re.search(r"\b(?:12v|m12)\b", lower):
            voltage_class = "12v"
        elif re.search(r"\b(?:flexvolt|60v)\b", lower):
            voltage_class = "60v"
        elif re.search(r"\b(?:36v|40v|xgt|multivolt)\b", lower):
            voltage_class = "36v_40v"
        elif re.search(r"\b24v\b", lower):
            voltage_class = "24v"
        elif re.search(r"\b(?:18v|20v|m18|lxt)\b|\b18\s*volt\b", lower):
            voltage_class = "18v_20v"
        elif re.search(r"\b(?:575|600)\s*v\b|\bauto[- ]line\b", lower):
            voltage_class = "575v"
        elif re.search(r"\b(?:460|480)\s*v\b", lower):
            voltage_class = "460_480v"
        elif re.search(r"\b(?:208|220|230|240)\s*v\b", lower):
            voltage_class = "230v"
        elif re.search(r"\b(?:110|115|120)\s*v\b", lower):
            voltage_class = "115v"

        hp_match = re.search(r"\b(\d+\s*-\s*\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*hp\b", lower)
        if hp_match:
            horsepower = _normalize_fraction_number(hp_match[1])

        ph_match = re.search(r"\b([13])\s*ph(?:ase)?\b", lower)
        if ph_match:
            phase = ph_match[1] + "ph"

        if re.search(r"\b(?:helical|hh|shelix|byrd)\b", lower):
            cutterhead = "helical"
        elif re.search(r"\bstraight\s+kni(?:fe|ves)\b", lower):
            cutterhead = "straight"

        if re.search(r"\bt[- ]glide\s+advance\b", lower):
            flags.add("t_glide_advance")
        if tool_kind == "table_saw":
            if re.search(r"\bindustrial\b[^-]*\b(?:cabinet|saw)\b|\bsawstop\s+industrial\b", lower):
                flags.add("industrial_saw")
            elif re.search(r"\bprofessional\b[^-]*\bcabinet\b|\bsawstop\s+professional\b", lower):
                flags.add("professional_saw")
            elif re.search(r"\bcontractor\b", lower):
                flags.add("contractor_saw")

        fence_match = re.search(
            r'\b(\d+)\s*(?:"|”|-?\s*in(?:ch|\.)?)\s*(?:rip|(?:industrial\s+|professional\s+|pro\s+)?t-glide|accu-fence|premium\s+fence|fence)\b',
            lower,
        )
        if fence_match:
            fence_inches = _normalize_fraction_number(fence_match[1])

        inch_matches = re.findall(
            r'(\d+\s*-\s*\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?:"|”|-?\s*in(?:ch|\.)?\b)',
            lower,
        )
        primary_inches = tuple(dict.fromkeys(_normalize_fraction_number(m) for m in inch_matches))
        model_nums = re.findall(r"(?<![0-9])(\d{3,4})(?![0-9])", re.sub(r"\b\d{3,5}\s*(?:rpm|opm|v|volt)\b", " ", lower))
        core_numbers = tuple(dict.fromkeys(n for n in model_nums if n not in {"110", "115", "120", "208", "220", "230", "240", "460", "480", "575"}))
    else:
        nums = re.findall(r"\b\d+(?:\.\d+)?\b", cleaned)
        core_numbers = tuple(dict.fromkeys(_normalize_fraction_number(n) for n in nums))

    return ProductSignature(
        catalog=item.catalog,
        equipment_class=eq_class,
        brand_key=brand_key,
        year=year,
        mpns=mpns,
        model_tokens=model_tokens,
        core_numbers=core_numbers,
        presence_flags=frozenset(flags),
        frame_tiers=frozenset(frame_tiers),
        drivetrains=frozenset(drivetrains),
        wheel_config=wheel_config,
        package_format=_package_format(item),
        tool_kind=tool_kind,
        power_mode=power_mode,
        voltage_class=voltage_class,
        horsepower=horsepower,
        phase=phase,
        cutterhead=cutterhead,
        fence_inches=fence_inches,
        primary_inches=primary_inches,
    )


def embedding_text(item: CanonicalItem, sig: ProductSignature | None = None) -> str:
    """Build deterministic canonical text for `text-embedding-3-large`."""
    sig = sig or extract_product_signature(item)
    cleaned = unicodedata.normalize("NFKC", item.name)
    cleaned = _STORE_BOILERPLATE_RE.sub(" ", cleaned)
    cleaned = _BRAKE_WIDTH_LIST_RE.sub(" ", cleaned)
    cleaned = _SKI_CM_IN_TITLE_RE.sub(" ", cleaned)
    cleaned = _BIKE_CM_SIZE_RE.sub(" ", cleaned)
    if item.catalog in {"cycling", "fresh-powder", "snow"}:
        cleaned = re.sub(
            r"\s+-\s+(?:(?:matte|gloss|satin|metallic|raw|solid)\s+)*"
            r"(?:black|white|red|blue|green|grey|gray|bone|charcoal|silver|gold|turquoise|emerald|tan|sand)"
            r"(?:\s+[a-z]+){0,2}\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    parts = [
        f"Catalog: {item.catalog}",
        f"Class: {sig.equipment_class}",
        f"Brand: {sig.brand_key or 'unknown'}",
        f"Product: {cleaned}",
    ]
    if sig.year:
        parts.append(f"ModelYear: {sig.year}")
    if sig.mpns:
        parts.append(f"MPN: {', '.join(sig.mpns)}")
    if sig.package_format:
        parts.append(f"Format: {sig.package_format}")
    if item.platform:
        parts.append(f"Platform: {item.platform}")
    return " | ".join(parts)


def are_items_compatible(
    a: CanonicalItem,
    b: CanonicalItem,
    sig_a: ProductSignature | None = None,
    sig_b: ProductSignature | None = None,
) -> bool:
    """Hard structural precision guards preventing false merges of distinct SKUs/years."""
    sig_a = sig_a or extract_product_signature(a)
    sig_b = sig_b or extract_product_signature(b)

    if sig_a.catalog != sig_b.catalog or sig_a.equipment_class != sig_b.equipment_class:
        return False
    if not sig_a.brand_key or sig_a.brand_key != sig_b.brand_key:
        return False

    # Model year guard: never merge different explicit years, and never merge legacy <=2024 with undated.
    if sig_a.year is not None and sig_b.year is not None:
        if sig_a.year != sig_b.year:
            return False
    elif sig_a.year is not None or sig_b.year is not None:
        explicit_year = sig_a.year if sig_a.year is not None else sig_b.year
        if explicit_year is not None and explicit_year <= 2024:
            return False
        if sig_a.catalog in {"fresh-powder", "snow"}:
            return False

    # Presence-required modifiers must match symmetrically
    if sig_a.presence_flags != sig_b.presence_flags:
        return False

    # Package format guard (Bare tool vs Kit vs Bundle)
    if sig_a.package_format and sig_b.package_format and sig_a.package_format != sig_b.package_format:
        return False

    # Explicit MPN guard when both specify MPNs
    shared_mpn = bool(sig_a.mpns and sig_b.mpns and (set(sig_a.mpns) & set(sig_b.mpns)))
    if sig_a.mpns and sig_b.mpns and not shared_mpn:
        return False

    # Distinctive model-family token overlap (Szymkiewicz-Simpson >= 0.67) unless bound by exact MPN
    if not shared_mpn:
        if not sig_a.model_tokens or not sig_b.model_tokens:
            return False
        set_a, set_b = set(sig_a.model_tokens), set(sig_b.model_tokens)
        overlap = len(set_a & set_b) / min(len(set_a), len(set_b))
        if overlap < 0.67:
            return False

    if sig_a.catalog in {"fresh-powder", "snow"}:
        if sig_a.core_numbers != sig_b.core_numbers:
            return False

    elif sig_a.catalog == "cycling":
        if sig_a.frame_tiers and sig_b.frame_tiers and sig_a.frame_tiers != sig_b.frame_tiers:
            return False
        if sig_a.drivetrains and sig_b.drivetrains and sig_a.drivetrains != sig_b.drivetrains:
            return False
        if sig_a.wheel_config and sig_b.wheel_config and sig_a.wheel_config != sig_b.wheel_config:
            return False
        if sig_a.core_numbers != sig_b.core_numbers:
            return False

    elif sig_a.catalog == "tools":
        if sig_a.tool_kind and sig_b.tool_kind and sig_a.tool_kind != sig_b.tool_kind:
            return False
        if sig_a.power_mode and sig_b.power_mode and sig_a.power_mode != sig_b.power_mode:
            return False
        if sig_a.voltage_class and sig_b.voltage_class and sig_a.voltage_class != sig_b.voltage_class:
            return False
        if sig_a.horsepower and sig_b.horsepower and sig_a.horsepower != sig_b.horsepower:
            return False
        if sig_a.phase and sig_b.phase and sig_a.phase != sig_b.phase:
            return False
        if sig_a.cutterhead and sig_b.cutterhead and sig_a.cutterhead != sig_b.cutterhead:
            return False
        if sig_a.fence_inches and sig_b.fence_inches and sig_a.fence_inches != sig_b.fence_inches:
            return False
        if sig_a.primary_inches and sig_b.primary_inches and sig_a.primary_inches != sig_b.primary_inches:
            return False
        if not shared_mpn and sig_a.core_numbers and sig_b.core_numbers and sig_a.core_numbers != sig_b.core_numbers:
            return False

    # Same-currency reference price sanity guard
    if (
        a.currency == b.currency
        and a.original_price
        and b.original_price
        and a.original_price > 0
        and b.original_price > 0
    ):
        ratio = max(a.original_price, b.original_price) / min(a.original_price, b.original_price)
        if ratio > 1.35:
            return False

    return True


def _canonical_group_label(items: Sequence[CanonicalItem], sig: ProductSignature) -> str:
    brand_display = next(
        (it.brand.strip() for it in items if it.brand and it.brand != "Unspecified"),
        sig.brand_key.title(),
    )
    scored = []
    for it in items:
        title = re.sub(r"\s+", " ", _STORE_BOILERPLATE_RE.sub(" ", it.name)).strip()
        has_brand = title.casefold().startswith(brand_display.casefold())
        scored.append((not has_brand, len(title), title))
    best_title = min(scored)[2]
    if brand_display and not best_title.casefold().startswith(brand_display.casefold()):
        best_title = f"{brand_display} {best_title}"
    return best_title


def build_equivalence_index(
    items: Sequence[CanonicalItem],
    *,
    embedder: Embedder,
    catalog: str | None = None,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    mpn_similarity_threshold: float = DEFAULT_MPN_SIMILARITY_THRESHOLD,
    min_stores: int = 2,
) -> EquivalenceIndex:
    """Embed catalog items (with caching) and cluster equivalent products across retailers."""
    item_list = list(items)
    catalog_slug = catalog or (item_list[0].catalog if item_list else "")
    known_brands = {normalize_brand_key(it.brand) for it in item_list if normalize_brand_key(it.brand)}
    signatures = [extract_product_signature(it, known_brands=known_brands) for it in item_list]
    texts = [embedding_text(it, sig) for it, sig in zip(item_list, signatures, strict=True)]
    vectors = embedder.embed_texts(texts)

    blocks: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for idx, sig in enumerate(signatures):
        if sig.brand_key:
            blocks[(sig.catalog, sig.equipment_class, sig.brand_key)].append(idx)

    clusters: dict[int, list[int]] = {i: [i] for i in range(len(item_list))}
    owner: list[int] = list(range(len(item_list)))

    candidate_edges: list[tuple[bool, float, int, int]] = []
    for indices in blocks.values():
        if len(indices) < 2:
            continue
        for pos_i in range(len(indices)):
            i = indices[pos_i]
            for pos_j in range(pos_i + 1, len(indices)):
                j = indices[pos_j]
                if item_list[i].url == item_list[j].url:
                    continue
                if not are_items_compatible(item_list[i], item_list[j], signatures[i], signatures[j]):
                    continue
                shared_mpn = bool(
                    signatures[i].mpns
                    and signatures[j].mpns
                    and (set(signatures[i].mpns) & set(signatures[j].mpns))
                )
                sim = _DOT(vectors[i], vectors[j])
                floor = mpn_similarity_threshold if shared_mpn else similarity_threshold
                if sim >= floor:
                    candidate_edges.append((shared_mpn, sim, i, j))

    candidate_edges.sort(key=lambda edge: (edge[0], edge[1]), reverse=True)

    for _shared_mpn, _sim, i, j in candidate_edges:
        ci, cj = owner[i], owner[j]
        if ci == cj:
            continue
        members_i, members_j = clusters[ci], clusters[cj]
        can_merge = True
        for u in members_i:
            for v in members_j:
                if not are_items_compatible(item_list[u], item_list[v], signatures[u], signatures[v]):
                    can_merge = False
                    break
                pair_mpn = bool(
                    signatures[u].mpns
                    and signatures[v].mpns
                    and (set(signatures[u].mpns) & set(signatures[v].mpns))
                )
                floor = mpn_similarity_threshold if pair_mpn else similarity_threshold
                if _DOT(vectors[u], vectors[v]) < floor:
                    can_merge = False
                    break
            if not can_merge:
                break
        if can_merge:
            merged = members_i + members_j
            clusters[ci] = merged
            del clusters[cj]
            for m in merged:
                owner[m] = ci

    groups: list[EquivalenceGroup] = []
    url_to_group: dict[str, EquivalenceGroup] = {}

    for member_indices in clusters.values():
        member_items = [item_list[idx] for idx in member_indices]
        distinct_stores = {it.store for it in member_items}
        if len(distinct_stores) < min_stores:
            continue
        sims = [
            _DOT(vectors[member_indices[a]], vectors[member_indices[b]])
            for a in range(len(member_indices))
            for b in range(a + 1, len(member_indices))
        ]
        rep_sig = signatures[member_indices[0]]
        years = sorted({signatures[idx].year for idx in member_indices if signatures[idx].year is not None})
        group_year = years[0] if len(years) == 1 else None
        all_mpns = tuple(dict.fromkeys(mpn for idx in member_indices for mpn in signatures[idx].mpns))
        canonical_name = _canonical_group_label(member_items, rep_sig)
        slug_seed = f"{rep_sig.catalog}:{rep_sig.brand_key}:{canonical_name}:{group_year or ''}"
        group_id = (
            re.sub(r"[^a-z0-9]+", "-", f"{rep_sig.brand_key}-{canonical_name}".casefold()).strip("-")[:56]
            + "-"
            + hashlib.sha256(slug_seed.encode("utf-8")).hexdigest()[:8]
        )

        prices_by_currency: dict[str, dict[str, float]] = {}
        for currency in sorted({it.currency for it in member_items}):
            cur_prices = [it.current_price for it in member_items if it.currency == currency and it.current_price > 0]
            if cur_prices:
                low, high = min(cur_prices), max(cur_prices)
                prices_by_currency[currency] = {
                    "low": round(low, 2),
                    "high": round(high, 2),
                    "spread": round(high - low, 2),
                }

        ordered_items = tuple(sorted(member_items, key=lambda it: (it.currency, it.current_price, it.store, it.url)))
        brand_label = next(
            (it.brand for it in ordered_items if it.brand and it.brand != "Unspecified"),
            rep_sig.brand_key.title(),
        )
        group = EquivalenceGroup(
            group_id=group_id,
            catalog=rep_sig.catalog,
            brand=brand_label,
            canonical_name=canonical_name,
            category=ordered_items[0].category,
            year=group_year,
            mpns=all_mpns,
            store_count=len(distinct_stores),
            listing_count=len(ordered_items),
            mean_similarity=round(sum(sims) / len(sims), 4) if sims else 1.0,
            min_similarity=round(min(sims), 4) if sims else 1.0,
            prices_by_currency=prices_by_currency,
            items=ordered_items,
        )
        groups.append(group)
        for it in ordered_items:
            url_to_group[it.url] = group

    groups.sort(
        key=lambda g: (
            -g.store_count,
            -max((p["spread"] for p in g.prices_by_currency.values()), default=0.0),
            g.canonical_name,
        )
    )
    return EquivalenceIndex(
        model=embedder.model,
        catalog=catalog_slug,
        items=item_list,
        signatures=signatures,
        vectors=vectors,
        groups=groups,
        url_to_group=url_to_group,
    )


def query_similar(
    index: EquivalenceIndex,
    target: CanonicalItem | str,
    *,
    embedder: Embedder,
    top_k: int = 5,
    require_compatible: bool = False,
) -> list[dict]:
    """Return nearest catalog listings by `text-embedding-3-large` cosine similarity."""
    if not index.items:
        return []
    if isinstance(target, CanonicalItem):
        target_sig = extract_product_signature(target)
        query_vec = embedder.embed_texts([embedding_text(target, target_sig)])[0]
    else:
        target_sig = None
        query_vec = embedder.embed_texts([target])[0]

    scored: list[tuple[float, int]] = []
    for idx, item in enumerate(index.items):
        if isinstance(target, CanonicalItem):
            if item.url == target.url:
                continue
            if require_compatible and not are_items_compatible(target, item, target_sig, index.signatures[idx]):
                continue
        scored.append((_DOT(query_vec, index.vectors[idx]), idx))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = []
    for sim, idx in scored[:top_k]:
        it = index.items[idx]
        results.append({
            "similarity": round(sim, 4),
            "store": it.store,
            "brand": it.brand,
            "name": it.name,
            "category": it.category,
            "current_price": it.current_price,
            "original_price": it.original_price,
            "currency": it.currency,
            "url": it.url,
        })
    return results


def load_deal_common_items(
    db_path: Path,
    catalog_slug: str,
    *,
    brand_aliases: tuple[tuple[str, str], ...] = (),
    freshness_hours: float | None = None,
    now: datetime | None = None,
) -> list[CanonicalItem]:
    """Load `CanonicalItem` records from a `deal_common` catalog SQLite snapshot (Big Ring, Bench Drop, etc.)."""
    if not Path(db_path).exists():
        return []
    anchor = now or datetime.now(UTC)
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        identity = conn.execute("SELECT catalog FROM metadata").fetchone()
        if identity is None or identity[0] != catalog_slug:
            raise ValueError(f"Snapshot at {db_path} does not match catalog {catalog_slug!r}")
        rows = conn.execute(
            "SELECT name, successful_at, status, products FROM sources WHERE status = 'complete' ORDER BY name"
        ).fetchall()

    items: list[CanonicalItem] = []
    for row in rows:
        if freshness_hours is not None:
            ts = row["successful_at"]
            if not ts:
                continue
            age = anchor - datetime.fromisoformat(ts)
            if not timedelta(0) <= age <= timedelta(hours=freshness_hours):
                continue
        store = row["name"]
        for prod in json.loads(row["products"]):
            offers = prod.get("offers") or []
            if not offers:
                continue
            best = min(
                offers,
                key=lambda o: (
                    -(o["original_price"] - o["current_price"]) / o["original_price"]
                    if o.get("original_price")
                    else 0.0,
                    o["current_price"],
                    o.get("variant_key", ""),
                ),
            )
            skus = []
            for offer in offers:
                for m in re.findall(r"\bSKU\s+([A-Za-z0-9_.-]+)", offer.get("options") or ""):
                    if m not in skus:
                        skus.append(m)
            sizes = tuple(
                dict.fromkeys(o["size"] for o in offers if o.get("size") and o.get("availability") == "in_stock")
            )
            brand = manufacturer_name(prod.get("brand"), brand_aliases=brand_aliases)
            items.append(
                CanonicalItem(
                    catalog=catalog_slug,
                    store=store,
                    url=prod["url"],
                    name=prod["name"],
                    brand=brand,
                    category=prod.get("category") or "",
                    format=prod.get("format") or "",
                    platform=prod.get("platform") or "",
                    currency="USD",
                    current_price=float(best["current_price"]),
                    original_price=float(best["original_price"]) if best.get("original_price") else None,
                    sizes=sizes,
                    skus=tuple(skus),
                )
            )
    return items


def load_fresh_powder_items(
    db_path: Path,
    *,
    categories: Sequence[str] = (
        "skis",
        "snowboards",
        "ski boots",
        "snowboard boots",
        "bindings",
        "goggles",
        "helmets",
    ),
) -> list[CanonicalItem]:
    """Load latest per-store equipment deals from FreshPowder `deals.db` without importing `aggregator`."""
    if not Path(db_path).exists():
        return []
    where_cat = ""
    params: tuple[str, ...] = ()
    if categories:
        where_cat = f"AND d.category IN ({','.join('?' for _ in categories)})"
        params = tuple(categories)
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT d.store, d.name, d.url, d.current_price, d.original_price,
                   d.category, d.sizes, d.brand, d.currency
            FROM deals d
            JOIN (SELECT store, MAX(scraped_at) AS ts FROM deals GROUP BY store) latest
              ON latest.store = d.store AND latest.ts = d.scraped_at
            WHERE d.exclusion_reason IS NULL
              AND d.current_price >= 1
              {where_cat}
            ORDER BY d.store, d.url
            """,
            params,
        ).fetchall()
    return [
        CanonicalItem(
            catalog="fresh-powder",
            store=row["store"],
            url=row["url"],
            name=row["name"],
            brand=row["brand"] or "",
            category=row["category"] or "",
            currency=row["currency"] or "USD",
            current_price=float(row["current_price"]),
            original_price=float(row["original_price"]) if row["original_price"] else None,
            sizes=tuple(s.strip() for s in (row["sizes"] or "").split(",") if s.strip()),
        )
        for row in rows
    ]


def serialize_index_summary(index: EquivalenceIndex, *, max_groups: int = 50) -> dict:
    """Produce a deterministic JSON-serializable summary of canonical equivalence groups."""
    return {
        "catalog": index.catalog,
        "embedding_model": index.model,
        "total_listings": len(index.items),
        "cross_retailer_groups": len(index.groups),
        "matched_listings": sum(g.listing_count for g in index.groups),
        "groups": [
            {
                "group_id": g.group_id,
                "brand": g.brand,
                "canonical_name": g.canonical_name,
                "category": g.category,
                "year": g.year,
                "mpns": list(g.mpns),
                "store_count": g.store_count,
                "listing_count": g.listing_count,
                "mean_similarity": g.mean_similarity,
                "min_similarity": g.min_similarity,
                "prices_by_currency": g.prices_by_currency,
                "offers": [asdict(it) for it in g.items],
            }
            for g in index.groups[:max_groups]
        ],
    }
