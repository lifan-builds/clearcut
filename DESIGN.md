# Design System — Clearcut Editions

## Visual Direction

Clearcut uses an **Expedition Monograph & Panoramic Field Portfolio** language across its front-door gallery (`common/deal_common/portal/`) and all three specialist equipment storefronts (`FreshPowder`, `Big Ring`, `Bench Drop`). One shared structural grammar—translucent top World Dock, rounded panoramic art plates, unified horizontal command consoles, and spacious gallery deal cards—adapts its material atmosphere to each terrain:

- **Front Door (`Clearcut`):** Ambient shifting ground (`#ecf2f8` -> `#eff3ed` -> `#f5f0e7`) carrying a three-monolith horizontal scroll-snap gallery reel (`900x1350` portrait plates) with damped spring physics (`cubic-bezier(0.22, 1, 0.36, 1)`), subtle scroll parallax, and zero premature form clutter.
- **Edition 01 — Snow (`FreshPowder`):** Glacier daylight ground (`#ecf2f8`), midnight-glacier ink (`#0b1b2b`), alpine cobalt (`#0f52b3`), and ski-patrol vermilion (`#cf3619`) for `50%+` markdowns.
- **Edition 02 — Cycling (`Big Ring`):** Limestone-tarmac ground (`#eff3ec`), racing-spruce ink (`#0e241b`), and peloton chartreuse (`#cef255`).
- **Edition 03 — Tools (`Bench Drop`):** Brushed shop-steel ground (`#eceff2`), cast-iron ink (`#17191c`), and anodized machinist amber (`#ffad42`).

## Typography

- **Display & Mastheads:** Self-hosted `Instrument Serif` (`400`, `-0.02em` tracking, `0.98` line-height) for hero titles, monolith headings, and editorial callouts.
- **Interface & Tabular Data:** Self-hosted variable `Manrope` (`400–800`) with `font-variant-numeric: tabular-nums` enforced across all prices, discount stamps, counts, and observation tables.

## Core Tokens & Surfaces

- **Radii:** `24px` panoramic heroes and monolith plates, `20px` command consoles and gallery deal cards, `11px` inputs, `999px` dock pills, discount stamps, and store CTAs.
- **Motion:** Single spring curve (`cubic-bezier(0.22, 1, 0.36, 1)`); full `prefers-reduced-motion: reduce` support.
- **Assets:** Repository-owned, text-free high-resolution plates with embedded generation provenance (`common/deal_common/portal/static/img/*-card.jpg` and per-catalog `*-banner.jpg`).
