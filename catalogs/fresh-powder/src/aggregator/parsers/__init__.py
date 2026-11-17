"""Store-specific HTML parsers for non-Shopify, non-browser retailers.

Shopify, SkiEssentials, and The Circle stores use parsers in deal_common.parsers.
Browser-rendered stores use JS extractors defined in aggregator.browser.
This module contains BS4-based parsers for the remaining stores:
  - alpineshopvt.py      (Alpine Shop VT — BigCommerce)
  - bluezone.py          (BlueZone Sports — custom HTML)
  - coloradodiscount.py  (Colorado Discount Skis — custom HTML)
  - sacredride.py        (Sacred Ride — WooCommerce)
"""
