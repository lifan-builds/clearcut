"""RA Cycles exact variants, warehouse stock and visible purchase evidence."""
import json
import math
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from deal_common.parsers.common import parse_price
from deal_common.parsers.shopify import ShopifyParser, _products_json

HOST = 'racycles.com'
_LOCATION_STATES = frozenset({'in', 'low-1', 'low-2', 'out', 'none'})
_SIZE_OPTIONS = frozenset({'size', 'frame size', 'framesize', 'bike size'})


def _one(values, label):
    values = list(values)
    if len(values) != 1 or values[0] is None:
        raise ValueError('Missing or ambiguous RA ' + label)
    return values[0]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate RA JSON key')
        result[key] = value
    return result


def _json(text):
    return json.loads(text, object_pairs_hook=_unique_object)


def _money(value):
    if isinstance(value, bool):
        raise TypeError('Invalid RA price')
    text = str(value).strip()
    if not re.fullmatch(r'\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?', text):
        raise ValueError('Invalid RA price')
    price = parse_price(text)
    if price is None or not math.isfinite(price) or price <= 0:
        raise ValueError('Invalid RA price')
    return price


def _minor(value, *, reference=False):
    if reference and (value is None or (type(value) is int and value == 0)):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('Invalid RA minor-unit price')
    return value / 100


def _destination(url, canonical):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    key = query.get('variant', [''])[0]
    if (parts.scheme != 'https' or parts.netloc != HOST or parts.fragment
            or parts.path != urlsplit(canonical).path or set(query) != {'variant'}
            or query['variant'] != [key] or not key.isdigit()):
        raise ValueError('Unsafe RA exact destination')
    return key


def detail_data(html: str, page_url: str, raw: dict | None = None) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    parts = urlsplit(page_url)
    if parts.scheme != 'https' or parts.netloc != HOST or not re.fullmatch(r'/products/[A-Za-z0-9_-]+', parts.path):
        raise ValueError('Unsafe RA product page')
    canonical = _one((n.get('href') for n in soup.select('link[rel="canonical"]')), 'canonical')
    if canonical != f'https://{HOST}{parts.path}':
        raise ValueError('RA canonical identity mismatch')
    main = _one(soup.select('tp-product-info.tp-product'), 'purchase panel')
    section_id = main.get('data-section-id', '')
    if main.get('data-product-url') != parts.path or not section_id:
        raise ValueError('RA purchase panel identity mismatch')
    name = _one((n.get_text(' ', strip=True) for n in main.select('h1.tp-product__title')), 'title')
    embedded = _json(_one((n.get_text() for n in main.select('script[data-product-json][type="application/json"]')), 'product data'))
    if not isinstance(embedded, dict):
        raise TypeError('Invalid RA product data')
    product_id = str(embedded.get('id'))
    option_names = embedded.get('options')
    embedded_variants = embedded.get('variants')
    if (not product_id.isdigit() or embedded.get('title') != name
            or '/products/' + str(embedded.get('handle')) != parts.path
            or embedded.get('requires_selling_plan') is not False
            or not isinstance(option_names, list) or not option_names
            or any(not isinstance(opt, str) or not opt.strip() for opt in option_names)
            or not isinstance(embedded_variants, list) or not embedded_variants):
        raise ValueError('RA embedded product identity mismatch')
    form = _one(main.select('form.tp-buy__form[action="/cart/add"]'), 'purchase form')
    if (_one((n.get('value') for n in form.select('input[name="product-id"]')), 'form product') != product_id
            or _one((n.get('value') for n in form.select('input[name="section-id"]')), 'form section') != section_id):
        raise ValueError('RA purchase form identity mismatch')
    purchase_input = _one(form.select('input[name="id"][data-variant-id]'), 'purchase variant')
    selected_key = purchase_input.get('value', '')
    if not selected_key.isdigit():
        raise ValueError('Invalid RA purchase variant')
    requested = parse_qs(parts.query).get('variant')
    if requested is not None and requested != [selected_key]:
        raise ValueError('RA requested variant not selected')
    quantity = _one(form.select('input[name="quantity"][data-qty-input]'), 'quantity')
    if quantity.get('value') != '1' or quantity.get('min') != '1':
        raise ValueError('Unsupported RA quantity')
    atc_button = _one(form.select('button[data-atc][name="add"]'), 'purchase button')
    picker = _one(main.select('[data-variant-picker]'), 'variant picker')
    fields = picker.select('fieldset[data-variant-option]')
    picker_labels = [_one((n.get_text(' ', strip=True) for n in field.select('legend.tp-variant-picker__label')), 'option label')
                     for field in fields]
    checked_options = [_one((n.get('value') for n in field.select('input[data-option-input][checked]')), 'checked option')
                       for field in fields]
    if ([field.get('data-option-index') for field in fields] != [str(i) for i in range(len(fields))]
            or picker_labels != option_names):
        raise ValueError('RA option selectors conflict with product options')
    recap = _one(main.select('[data-variant-recap]'), 'variant recap')
    recap_sku = _one((n.get_text(' ', strip=True) for n in recap.select('[data-recap-sku]')), 'recap SKU')
    recap_title = _one((n.get_text(' ', strip=True) for n in recap.select('[data-recap-title]')), 'recap option')
    status = _json(_one((n.get_text() for n in main.select('script[data-variant-stock][type="application/json"]')), 'stock map'))
    if not isinstance(status, dict) or not status:
        raise ValueError('Invalid RA stock map')
    location_stock = _json(_one((n.get_text() for n in main.select('script[data-location-stock][type="application/json"]')), 'location stock'))
    location_rows = main.select('[data-inventory-locations] [data-location-row]')
    if not isinstance(location_stock, dict) or not location_rows:
        raise ValueError('Invalid RA location stock')
    stock_box = _one(main.select('[data-stock-status]'), 'stock status')
    stock_lines = {}
    visible_lines = []
    for line in stock_box.select('[data-stock-line]'):
        code = line.get('data-stock-line', '')
        text = line.get_text(' ', strip=True)
        if not code or code in stock_lines or not text:
            raise ValueError('Invalid RA stock status line')
        stock_lines[code] = text
        if not line.has_attr('hidden'):
            visible_lines.append(code)
    if stock_lines.get('in') != 'In Stock Ships Immediately':
        raise ValueError('RA immediate stock wording changed')
    virtual_notice = stock_lines.get('vw', '')
    if not virtual_notice or 'sold out' in virtual_notice.casefold() or 'soldout' not in stock_lines:
        raise ValueError('Missing RA virtual warehouse or sold-out notice')
    schemas = []
    for script in soup.select('script[type="application/ld+json"]'):
        value = _json(script.get_text())
        if isinstance(value, dict) and value.get('@type') in {'ProductGroup', 'Product'}:
            schemas.append(value)
    schema = _one(schemas, 'product schema')
    schema_by_key = {}
    if schema['@type'] == 'ProductGroup':
        if str(schema.get('productGroupID')) != product_id or schema.get('name') != name or schema.get('url') != canonical:
            raise ValueError('RA product group identity mismatch')
        schema_variants = schema.get('hasVariant')
        if not isinstance(schema_variants, list) or not schema_variants:
            raise ValueError('Missing RA complete variant schema')
        for child in schema_variants:
            if not isinstance(child, dict) or child.get('@type') != 'Product' or not isinstance(child.get('offers'), dict):
                raise ValueError('Invalid RA variant schema')
            offer = child['offers']
            key = _destination(offer.get('url', ''), canonical)
            if key in schema_by_key or offer.get('priceCurrency') != 'USD' or not child.get('sku'):
                raise ValueError('Duplicate RA variant or unsupported currency/SKU')
            schema_by_key[key] = {
                'url': offer['url'], 'sku': child['sku'], 'name': child.get('name'),
                'price': _money(offer.get('price')),
                'availability': offer.get('availability', '').rsplit('/', 1)[-1],
            }
    else:
        if len(embedded_variants) != 1 or not isinstance(embedded_variants[0], dict):
            raise ValueError('RA single-product schema on multi-variant product')
        key = str(embedded_variants[0].get('id'))
        offer = schema.get('offers')
        if (not key.isdigit() or schema.get('name') != name or schema.get('url') != canonical
                or not schema.get('sku') or not isinstance(offer, dict) or offer.get('@type') != 'Offer'
                or offer.get('url') != canonical or offer.get('priceCurrency') != 'USD'):
            raise ValueError('Invalid RA single-product schema')
        schema_by_key[key] = {
            'url': f'{canonical}?variant={key}', 'sku': schema['sku'],
            'name': f"{name} - {' / '.join(embedded_variants[0].get('options') or [])}",
            'price': _money(offer.get('price')),
            'availability': offer.get('availability', '').rsplit('/', 1)[-1],
        }
    size_positions = [i for i, opt in enumerate(option_names) if opt.casefold() in _SIZE_OPTIONS]
    variants = {}
    for item in embedded_variants:
        if not isinstance(item, dict):
            raise TypeError('Invalid RA embedded variant')
        key = str(item.get('id'))
        if key not in schema_by_key or key in variants:
            raise ValueError('Incomplete or duplicate RA variant identity')
        schema_row = schema_by_key[key]
        state_code = status.get(key)
        if not isinstance(state_code, str) or not (
                state_code in {'in', 'vw', 'soldout'} or re.fullmatch(r'bo-[1-9]\d*', state_code)):
            raise ValueError('Missing or malformed RA variant stock')
        if state_code.startswith('bo-') and state_code not in stock_lines:
            raise ValueError('RA missing backorder notice')
        locs = location_stock.get(key)
        if (not isinstance(locs, list) or len(locs) != len(location_rows)
                or any(loc not in _LOCATION_STATES for loc in locs)):
            raise ValueError('Missing or malformed RA location stock')
        available = item.get('available')
        if type(available) is not bool:
            raise ValueError('Invalid RA variant availability')
        expected_stock = ((False, 'OutOfStock') if state_code == 'soldout'
                          else (True, 'BackOrder') if state_code.startswith('bo-')
                          else (True, 'InStock'))
        if (available, schema_row['availability']) != expected_stock:
            raise ValueError('Conflicting RA structured stock')
        options = item.get('options')
        price = _minor(item.get('price'))
        reference = _minor(item.get('compare_at_price'), reference=True)
        if (not isinstance(options, list) or len(options) != len(option_names)
                or any(not isinstance(v, str) or not v for v in options)
                or item.get('title') != ' / '.join(options)
                or item.get('name') != name + ' - ' + ' / '.join(options)
                or schema_row['name'] != item['name']
                or item.get('sku') != schema_row['sku']
                or price != schema_row['price']
                or item.get('requires_selling_plan') is not False
                or item.get('quantity_rule', {}).get('min') != 1
                or item.get('quantity_rule', {}).get('increment') != 1):
            raise ValueError('RA embedded and structured variant conflict')
        variants[key] = {
            'url': schema_row['url'], 'sku': schema_row['sku'], 'name': schema_row['name'],
            'price': price, 'reference': reference, 'available': available,
            'stock_state': state_code, 'location_stock': locs,
            'virtual_warehouse': state_code == 'vw', 'in_stock': state_code == 'in',
            'options': ' / '.join(v for v in options if v != 'Default Title'),
            'raw_options': options,
            'size': ' / '.join(options[i] for i in size_positions) or None,
        }
    if (set(variants) != set(schema_by_key) or set(variants) != set(status)
            or set(variants) != set(location_stock) or selected_key not in variants):
        raise ValueError('Incomplete RA variant identity sets')
    state = variants[selected_key]
    if (checked_options != state['raw_options'] or recap_sku != state['sku']
            or recap_title != ' / '.join(state['raw_options'])):
        raise ValueError('RA checked options conflict with selected variant')
    if (purchase_input.has_attr('disabled') != (not state['available'])
            or atc_button.has_attr('disabled') != (not state['available'])):
        raise ValueError('RA purchase controls conflict with availability')
    if stock_box.get('data-state') != state['stock_state'] or visible_lines != [state['stock_state']]:
        raise ValueError('RA displayed stock conflicts with selected variant')
    for index, row in enumerate(location_rows):
        visible_loc = [n.get('data-location-state')
                       for n in row.select('.tp-inventory-location__state[data-location-state]')
                       if not n.has_attr('hidden')]
        if visible_loc != [state['location_stock'][index]]:
            raise ValueError('RA displayed location stock conflicts with selected variant')
    price_box = _one(main.select('[data-product-price]'), 'price panel')
    current_box = _one(price_box.select('[data-price-current]'), 'displayed current price')
    displayed_price = _money(_one((n.get_text(' ', strip=True) for n in current_box.select('[data-price-amount]')), 'displayed price'))
    if displayed_price != state['price']:
        raise ValueError('RA selected identity, price or reference conflict')
    compare_nodes = price_box.select('[data-price-compare]')
    visible_compares = [n for n in compare_nodes if not n.has_attr('hidden')]
    on_sale = state['reference'] is not None and state['reference'] > state['price']
    if ('tp-product__price-current--sale' in (current_box.get('class') or [])) != on_sale:
        raise ValueError('RA selected identity, price or reference conflict')
    if on_sale:
        compare_box = _one(visible_compares, 'displayed reference')
        displayed_ref = _money(_one((n.get_text(' ', strip=True) for n in compare_box.select('[data-price-amount]')), 'displayed reference amount'))
        if displayed_ref != state['reference']:
            raise ValueError('RA selected identity, price or reference conflict')
    elif visible_compares:
        raise ValueError('RA selected identity, price or reference conflict')
    if raw is not None:
        if (not isinstance(raw, dict) or str(raw.get('id')) != product_id or raw.get('title') != name
                or '/products/' + str(raw.get('handle')) != parts.path or not isinstance(raw.get('variants'), list)):
            raise ValueError('RA product endpoint identity mismatch')
        option_defs = raw.get('options')
        if (not isinstance(option_defs, list)
                or any(not isinstance(n, dict) or not n.get('name') for n in option_defs)
                or [n['name'] for n in option_defs] != option_names):
            raise ValueError('RA option definitions conflict with visible selectors')
        seen = set()
        for item in raw['variants']:
            if not isinstance(item, dict):
                raise TypeError('RA endpoint and purchase variant conflict')
            key = str(item.get('id'))
            if key not in variants or key in seen:
                raise ValueError('RA product endpoint variant identity mismatch')
            seen.add(key)
            actual = variants[key]
            if (item.get('options') != actual['raw_options']
                    or item.get('sku') != actual['sku'] or item.get('available') != actual['available']
                    or _minor(item.get('price')) != actual['price']
                    or _minor(item.get('compare_at_price'), reference=True) != actual['reference']
                    or item.get('name') != actual['name']
                    or item.get('requires_selling_plan') is not False
                    or item.get('quantity_rule', {}).get('min') != 1
                    or item.get('quantity_rule', {}).get('increment') != 1):
                raise ValueError('RA endpoint and purchase variant conflict')
        if seen != set(variants):
            raise ValueError('Incomplete RA endpoint variants')
    final_sale = [
        _one((t.get_text(' ', strip=True) for t in node.select('.tp-product__accordion-title')), 'final-sale title')
        for node in main.select('details.tp-product__accordion')
        if 'tp-final-sale' in (node.get('class') or [])
        or 'final sale' in node.get_text(' ', strip=True).casefold()
    ]
    sale_note = _one(final_sale, 'final-sale notice') if final_sale else ''
    if sale_note and 'final sale' not in sale_note.casefold():
        raise ValueError('Invalid RA final-sale notice')
    for variant in variants.values():
        code = variant['stock_state']
        notice = (virtual_notice if variant['virtual_warehouse']
                  else stock_lines[code] if code.startswith('bo-') else '')
        variant['shipping_note'] = ' '.join(n for n in (notice, sale_note) if n)
        variant.pop('raw_options', None)
    return {'url': canonical, 'name': name, 'product_id': product_id, 'currency': 'USD',
            'selected_key': selected_key, 'variants': variants, 'final_sale_note': sale_note}


class RacyclesShopifyParser(ShopifyParser):
    requires_product_pages = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.products = {}
        self.variant_ids = {}
        self.product_ids = {}

    def parse_listing_page(self, html, page_url):
        products = super().parse_listing_page(html, page_url)
        raw = {item['handle']: item for item in _products_json(html) if item.get('handle')}
        for product in products:
            self.products[product.url] = product
            item = raw[urlsplit(product.url).path.rsplit('/', 1)[-1]]
            if not isinstance(item.get('variants'), list) or any(not isinstance(v, dict) for v in item['variants']):
                raise ValueError('Invalid RA feed variants')
            keys = [str(v.get('id')) for v in item['variants']]
            if len(keys) != len(set(keys)) or any(not key.isdigit() for key in keys) or not str(item.get('id')).isdigit():
                raise ValueError('Invalid RA feed identities')
            self.variant_ids[product.url] = set(keys)
            self.product_ids[product.url] = str(item['id'])
        return products

    def get_product_evidence_url(self, html, page_url):
        data = detail_data(html, page_url)
        return data['url'] + '.js?currency=USD'

    def parse_product_page(self, html, page_url):
        raise ValueError('RA exact product endpoint evidence required')

    def parse_product_evidence(self, html, page_url, evidence):
        actual = detail_data(html, page_url, _json(evidence))
        product = self.products.get(page_url)
        if (product is None or product.name != actual['name']
                or self.product_ids.get(page_url) != actual['product_id']
                or self.variant_ids.get(page_url) != set(actual['variants'])):
            raise ValueError('RA listing and detail identity mismatch')
        offers = []
        for offer in product.offers:
            row = actual['variants'].get(offer.variant_key)
            if (row is None or offer.current_price != row['price'] or offer.original_price != row['reference']
                    or offer.options != row['options'] or (offer.size and offer.size != row['size'])):
                raise ValueError('RA listing and detail offer conflict')
            if not row['available']:
                continue
            offers.append(replace(offer, availability='in_stock' if row['in_stock'] else 'unknown',
                                  availability_note=row['shipping_note']))
        if not offers:
            return []
        best = min(offers, key=lambda o: (-o.discount_pct, o.current_price, o.variant_key))
        sizes = list(dict.fromkeys(o.size for o in offers if o.size
                                   and (o.current_price, o.original_price) == (best.current_price, best.original_price)))
        return [replace(product, current_price=best.current_price, original_price=best.original_price,
                        sizes=sizes or None, image_url=best.image_url, offers=offers)]
