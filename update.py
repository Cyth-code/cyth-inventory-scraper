# -*- coding: utf-8 -*-
"""
update.py — Combined CMS + Store ribbon update

Two steps:
  1. Push all scraped data to Wix CMS collection (Import912)
     - comp_data is the source of truth
     - No comparison with Wix Store products
  2. Update product ribbons in Wix Store catalog
"""

import pandas as pd
import datetime
import os
import json
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

script_dir = Path(__file__).resolve().parent
os.chdir(script_dir)

today = datetime.datetime.now().strftime('%Y-%m-%d')

# ── Config ─────────────────────────────────────────────────────────────────────
COLLECTION_ID  = 'Import912'
WIX_CMS_BASE   = 'https://www.wixapis.com/wix-data/v2/items'
WIX_STORE_BASE = 'https://www.wixapis.com/stores/v1'

def get_wix_headers():
    return {
        'Authorization': os.environ['WIX_API_KEY'],
        'wix-site-id':   os.environ['WIX_SITE_ID'],
        'Content-Type':  'application/json'
    }

# ── Safe value helpers ─────────────────────────────────────────────────────────

def safe_float(val, default=0.0):
    try:
        v = float(val)
        if v != v or v == float('inf') or v == float('-inf'):
            return default
        return v
    except:
        return default

def safe_str(val, default='NA'):
    if val is None or (isinstance(val, float) and val != val):
        return default
    s = str(val).strip()
    return s if s and s != 'nan' else default

def safe_bool(val):
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('true', '1', 'yes')

# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — CMS Collection Update
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_cms_items():
    print("Fetching existing CMS items...")
    sku_to_item = {}
    offset      = 0
    limit       = 100

    while True:
        body = {
            'dataCollectionId': COLLECTION_ID,
            'query': {'paging': {'limit': limit, 'offset': offset}}
        }
        response = requests.post(
            'https://www.wixapis.com/wix-data/v2/items/query',
            headers=get_wix_headers(),
            json=body,
            timeout=15
        )

        if response.status_code != 200:
            print(f"  CMS fetch failed: {response.status_code} {response.text}")
            break

        items = response.json().get('dataItems', [])
        for item in items:
            sku = item.get('data', {}).get('sku', '').strip()
            if sku:
                sku_to_item[sku] = item.get('id')

        print(f"  Fetched {offset + len(items)} CMS items...")
        if not items:
            break
        offset += limit

    print(f"  Total CMS items: {len(sku_to_item)}\n")
    return sku_to_item


def update_cms_item(item_id, data):
    body = {
        'dataCollectionId': COLLECTION_ID,
        'dataItem': {'id': item_id, 'data': data}
    }
    try:
        response = requests.put(
            f'{WIX_CMS_BASE}/{item_id}',
            headers=get_wix_headers(),
            json=body,
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        print(f"  CMS update error {item_id}: {e}")
        return False


def create_cms_item(data):
    body = {
        'dataCollectionId': COLLECTION_ID,
        'dataItem': {'data': data}
    }
    try:
        response = requests.post(
            WIX_CMS_BASE,
            headers=get_wix_headers(),
            json=body,
            timeout=10
        )
        return response.status_code in (200, 201)
    except Exception as e:
        print(f"  CMS create error: {e}")
        return False


def process_cms_sku(sku, row, sku_to_item):
    try:
        combined_status = safe_str(row.get('combined_status'), default='')
        combined_stock  = safe_str(row.get('combined_stock'), default='')

        # Skip unscraped SKUs
        if combined_status in ('', 'NA'):
            return sku, 'skipped'

        data = {
            'sku':                sku,
            'digikey_url':        safe_str(row.get('digikey_url')),
            'digikey_inventory':  safe_float(row.get('digikey_inventory')),
            'digikey_price':      safe_float(row.get('digikey_price')),
            'digikey_status':     safe_str(row.get('digikey_status')),
            'newark_url':         safe_str(row.get('newark_url')),
            'newark_inventory':   safe_float(row.get('newark_inventory')),
            'newark_price':       safe_float(row.get('newark_price')),
            'newark_status':      safe_str(row.get('newark_status')),
            'combined_inventory': safe_float(row.get('combined_inventory')),
            'InStock':            safe_bool(row.get('InStock', False)),
            'combined_status':    combined_status,
            'last_updated':       safe_str(row.get('last_updated'), default=today),
        }

        item_id = sku_to_item.get(sku)
        if item_id:
            success = update_cms_item(item_id, data)
            return sku, 'updated' if success else 'failed'
        else:
            success = create_cms_item(data)
            return sku, 'created' if success else 'failed'

    except Exception as e:
        print(f"  CMS error on {sku}: {e}")
        return sku, 'failed'


def run_cms_update(dfOutput):
    print("═" * 50)
    print("STEP 1 — Updating CMS Collection")
    print("═" * 50)

    sku_to_item = fetch_all_cms_items()
    results     = {'updated': 0, 'created': 0, 'skipped': 0, 'failed': 0}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(process_cms_sku, sku, dfOutput.loc[sku].to_dict(), sku_to_item): sku
            for sku in dfOutput.index
        }
        for i, fut in enumerate(as_completed(futures), 1):
            sku, result = fut.result()
            results[result] += 1
            if result in ('updated', 'created'):
                print(f"  [{i}/{len(futures)}] ✓ {sku} ({result})")
            elif result == 'failed':
                print(f"  [{i}/{len(futures)}] ! {sku} — failed")

    print(f"\nCMS Done — Updated: {results['updated']} | Created: {results['created']} | Skipped: {results['skipped']} | Failed: {results['failed']}\n")
    return results

# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — Wix Store Ribbon Update
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_wix_products():
    print("Fetching Wix Store products...")
    sku_to_id = {}
    offset    = 0
    limit     = 100

    while True:
        body = {'query': {'paging': {'limit': limit, 'offset': offset}}}
        response = requests.post(
            f'{WIX_STORE_BASE}/products/query',
            headers=get_wix_headers(),
            json=body,
            timeout=15
        )

        if response.status_code != 200:
            print(f"  Store fetch failed: {response.status_code} {response.text}")
            break

        products = response.json().get('products', [])
        for product in products:
            product_id = product.get('id')
            sku = product.get('sku', '').strip()
            if sku:
                sku_to_id[sku] = product_id
            for variant in product.get('variants', []):
                v_sku = variant.get('variant', {}).get('sku', '').strip()
                if v_sku:
                    sku_to_id[v_sku] = product_id

        print(f"  Fetched {offset + len(products)} store products...")
        if not products:
            break
        offset += limit

    print(f"  Total Store products: {len(sku_to_id)}\n")
    return sku_to_id


def update_store_ribbon(product_id, ribbon):
    payload = {'product': {'ribbon': ribbon if ribbon else ''}}
    try:
        response = requests.patch(
            f'{WIX_STORE_BASE}/products/{product_id}',
            headers=get_wix_headers(),
            json=payload,
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        print(f"  Store update error {product_id}: {e}")
        return False


def process_store_sku(sku, row, sku_to_id):
    try:
        combined_stock  = safe_str(row.get('combined_stock'), default='')
        combined_status = safe_str(row.get('combined_status'), default='')

        if combined_status in ('', 'NA'):
            return sku, 'skipped'

        # Never show ribbon for obsolete
        ribbon = 'Ships in 3-5 Days' if combined_stock == 'Active' and combined_status != 'Obsolete' else None

        # Try exact SKU match
        product_id = sku_to_id.get(sku)

        # Try with leading zero after dash (e.g. 150275-1R5 → 150275-01R5)
        if not product_id:
            parts = sku.split('-')
            if len(parts) == 2 and not parts[1].startswith('0'):
                alt_sku = f"{parts[0]}-0{parts[1]}"
                product_id = sku_to_id.get(alt_sku)

        if not product_id:
            return sku, 'notfound'

        success = update_store_ribbon(product_id, ribbon)
        return sku, 'updated' if success else 'failed'

    except Exception as e:
        print(f"  Store error on {sku}: {e}")
        return sku, 'failed'


def run_store_update(dfOutput):
    print("═" * 50)
    print("STEP 2 — Updating Store Ribbons")
    print("═" * 50)

    sku_to_id = fetch_all_wix_products()
    results   = {'updated': 0, 'notfound': 0, 'skipped': 0, 'failed': 0}
    not_found = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(process_store_sku, sku, dfOutput.loc[sku].to_dict(), sku_to_id): sku
            for sku in dfOutput.index
        }
        for i, fut in enumerate(as_completed(futures), 1):
            sku, result = fut.result()
            results[result] += 1
            if result == 'updated':
                print(f"  [{i}/{len(futures)}] ✓ {sku}")
            elif result == 'notfound':
                not_found.append(sku)
                print(f"  [{i}/{len(futures)}] ✗ {sku} — not in store")
            elif result == 'failed':
                print(f"  [{i}/{len(futures)}] ! {sku} — failed")

    print(f"\nStore Done — Updated: {results['updated']} | Not found: {results['notfound']} | Skipped: {results['skipped']} | Failed: {results['failed']}\n")
    return results, not_found

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def update_catalog():
    try:
        dfOutput = pd.read_csv('comp_data.csv', index_col='sku')
    except FileNotFoundError:
        raise SystemExit("Error: comp_data.csv not found.")

    print(f"Loaded {len(dfOutput)} SKUs from comp_data.csv\n")

    # Step 1 — CMS update
    cms_results = run_cms_update(dfOutput)

    # Step 2 — Store ribbon update
    store_results, not_found_skus = run_store_update(dfOutput)

    # Stock breakdown
    in_stock  = int((dfOutput['combined_stock'] == 'Active').sum()) if 'combined_stock' in dfOutput.columns else 0
    obsolete  = int((dfOutput['combined_status'] == 'Obsolete').sum()) if 'combined_status' in dfOutput.columns else 0
    out_stock = int(len(dfOutput) - in_stock - obsolete)

    print("═" * 50)
    print("SUMMARY")
    print("═" * 50)
    print(f"  In Stock:     {in_stock}")
    print(f"  Out of Stock: {out_stock}")
    print(f"  Obsolete:     {obsolete}")

    scrape_summary = {}
    if os.path.exists('scrape_summary.json'):
        with open('scrape_summary.json') as f:
            scrape_summary = json.load(f)

    full_summary = {
        **scrape_summary,
        'cms_updated':    cms_results['updated'],
        'cms_created':    cms_results['created'],
        'wix_updated':    store_results['updated'],
        'wix_notfound':   store_results['notfound'],
        'wix_skipped':    store_results['skipped'],
        'wix_failed':     store_results['failed'],
        'in_stock':       in_stock,
        'out_of_stock':   out_stock,
        'obsolete':       obsolete,
        'not_found_skus': not_found_skus[:20],
    }

    with open('full_summary.json', 'w') as f:
        json.dump(full_summary, f)


if __name__ == '__main__':
    update_catalog()
