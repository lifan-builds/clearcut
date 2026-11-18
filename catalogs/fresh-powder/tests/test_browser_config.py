"""Tests for browser.py store config registry."""

from aggregator.browser import STORE_CONFIGS, _parse_raw_products


def test_backcountry_plain_prices_keep_pages_nonempty_without_inventing_deals():
    import json
    import subprocess
    from pathlib import Path

    from aggregator.scraper import _products_to_deals

    fixture = json.loads((Path(__file__).parent / 'fixtures/backcountry_price_cards.json').read_text())
    # Minimal DOM surface for the captured card fields; run the actual extractor.
    setup = """
    const cards = JSON.parse(process.argv[1]);
    const document = {querySelectorAll: () => cards.map(card => ({
        querySelector: selector => ({
            'a.chakra-linkbox__overlay': {href: card.url},
            '.price': {textContent: card.price},
            '[data-id="title"]': {textContent: card.name},
            '[data-id="brandName"]': {textContent: card.brand},
            'img[data-id="image"]': {src: card.image, getAttribute: () => null},
        })[selector] || null,
        querySelectorAll: () => [],
    }))};
    """
    extractor = STORE_CONFIGS['backcountry'][1]

    def extract(cards):
        result = subprocess.check_output(
            ['node', '-e', setup + 'console.log(JSON.stringify((' + extractor + ')()));', json.dumps(cards)],
            text=True,
        )
        return _parse_raw_products(json.loads(result), fixture['source'])

    products = extract(fixture['cards'])
    assert len(products) == 4
    assert [p.current_price for p in products] == [1337.91, 999.95, 2629.89, 552.47]
    assert [p.original_price for p in products] == [2229.85, None, None, None]
    assert products[1].name == 'Black Diamond Impulse Ti 98 Skis - 2027'
    assert products[1].image_url == 'https://content.backcountry.com/images/items/large/BLD/BLDZAAD/MIDBLU.jpg'
    deals = _products_to_deals(products, 'Backcountry')
    assert len(deals) == 1
    assert deals[0].url == fixture['cards'][0]['url']

    # A whole page without reference prices is valid inventory, not an extraction failure.
    plain_products = extract(fixture['cards'][1:])
    assert len(plain_products) == 3
    assert _products_to_deals(plain_products, 'Backcountry') == []

    # Synthetic malformed/whole-dollar cases supplement the captured decimal prices.
    synthetic = [dict(fixture['cards'][1], price=price, url=f'https://www.backcountry.com/item-{i}')
                 for i, price in enumerate(['$1,200', 'Original price: $1,200', 'Current price: unavailable', 'See price in cart'])]
    assert [p.current_price for p in extract(synthetic)] == [1200]


def test_all_expected_configs_exist():
    from aggregator.config import STORES

    active_browser_types = {store.parser_type for store in STORES if store.use_browser}
    assert active_browser_types | {"bigcommerce"} == set(STORE_CONFIGS.keys())


def test_aliases():
    assert STORE_CONFIGS["corbetts"] is STORE_CONFIGS["bigcommerce"]
    assert STORE_CONFIGS["peterglenn"] is STORE_CONFIGS["bigcommerce"]


def test_config_tuple_shape():
    for name, config in STORE_CONFIGS.items():
        assert len(config) == 3, f"{name} config should be (wait_sel, js, next_sel)"
        wait_sel, js_extract, next_sel = config
        assert isinstance(wait_sel, str) and wait_sel
        assert isinstance(js_extract, str) and js_extract
        assert next_sel is None or isinstance(next_sel, str)


def test_parse_raw_products():
    raw = [
        {"name": "Test Ski", "url": "/product/test", "current_price": 499.99,
         "original_price": 599.99},
        {"name": "", "url": "/bad", "current_price": 100},  # empty name, should be filtered
        {"name": "No Price", "url": "/bad2", "current_price": None},  # no price, filtered
    ]
    products = _parse_raw_products(raw, "https://example.com")
    assert len(products) == 1
    assert products[0].name == "Test Ski"
    assert products[0].url == "https://example.com/product/test"


def test_parse_raw_products_absolute_url():
    raw = [{"name": "Abs URL", "url": "https://store.com/product",
            "current_price": 100, "original_price": None}]
    products = _parse_raw_products(raw, "https://example.com")
    assert products[0].url == "https://store.com/product"


def test_parse_raw_products_recovers_placeholder_name_from_url():
    raw = [{
        "name": "Gearhead Top Pick",
        "url": "https://www.backcountry.com/nordica-speedmachine-j1-ski-boot-2023-girls",
        "current_price": 105.0,
        "original_price": 150.0,
    }]
    products = _parse_raw_products(raw, "https://www.backcountry.com")
    assert products[0].name == "Nordica Speedmachine J1 Ski Boot 2023 Girls"


def test_parse_raw_products_prefixes_model_only_name_from_url_brand():
    raw = [{
        "name": "DX84 Ski",
        "url": "https://www.backcountry.com/kastle-dx84-ski",
        "current_price": 279.5,
        "original_price": 430.0,
    }]
    products = _parse_raw_products(raw, "https://www.backcountry.com")
    assert products[0].name == "Kastle DX84 Ski"
