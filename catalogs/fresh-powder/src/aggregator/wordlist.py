"""Snow and ski-themed word list and invite code generator."""

from __future__ import annotations

import random
import secrets

SNOW_WORDS = [
    "POWDER", "ALPINE", "SUMMIT", "GLACIER", "CORNICE", "MOGUL",
    "HALFPIPE", "RIDGELINE", "TREELINE", "COULOIR", "CIRQUE",
    "CHUTE", "GULLY", "BASIN", "BOWL", "PEAK", "CREST", "COL",
    "BLIZZARD", "FRESH", "GROOMERS", "CRUD", "CORN", "SLUSH",
    "CRUST", "HARDPACK", "CHAMPAGNE", "DEEP",
    "CARVE", "SHRED", "SLASH", "SEND", "CHARGE", "RIPP",
    "PIVOT", "JUMP", "DRIFT", "GLIDE", "TUCK",
    "BINDING", "EDGE", "BASE", "TOPSHEET", "CAMBER", "ROCKER",
    "SIDECUT", "SHOVEL", "TAIL", "BOOT", "LINER", "BUCKLE",
    "STORM", "VERTICAL", "LIFT", "GONDOLA", "TBAR",
    "PATROL", "ICON", "LEGEND", "STEEZE", "STYLE",
    "STOKE", "RIPPING", "SHREDDER", "GNAR", "PILLOW",
    "WINTER", "FROST", "FLURRY", "FREEZE",
    "ARCTIC", "NORDIC", "POLAR",
    "WHITE", "SILVER", "COBALT", "AZURE", "CRYSTAL", "SLATE",
    "DIAMOND", "TEAL", "STEEL",
]


def generate_readable_code() -> str:
    """Generate a snow-themed invite code like POWDER-FROST-42."""
    word1 = random.choice(SNOW_WORDS)
    word2 = random.choice(SNOW_WORDS)
    num = secrets.randbelow(90) + 10
    return f"{word1}-{word2}-{num}"
