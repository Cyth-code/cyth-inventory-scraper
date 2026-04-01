# -*- coding: utf-8 -*-
"""
update.py — Optimized parallel version with automatic Wix product fetch

Logic:
  - Fetches ALL products from Wix via API at the start
  - Builds SKU → Product ID map
  - Updates ribbon and stock status in parallel (10 at a time)
  - Clears ribbon for obsolete products
  - Tries leading zero SKU variation if not found
"""

import pandas as pd
import datetime
import os
import json
import requests
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

script_dir = Path(__file__).resolve().parent
os.chdir(script_dir)

# ── Status HTML ────────────────────────────────────────────────────────────────
inStockText    = '<p style="color: #008000;"><strong>In-Stock Ready-to-Ship'
outOfStockText = '<p style="color: #000000;"><strong>Available to Order</strong></p> '
obsoleteText   = '<p style="color: #FF0000;"><strong>OBSOLETE - CONTACT CYTH</strong></p>'

today = datetime.datetime.now().strftime('%Y-%m-%d')

# ── Wix REST API ───────────────────────────────────────────────────────────────
WIX_API_BASE = 'https://www.wixapis.com/stores/v1'

def get_wix_headers():
    return {
        'Authorization': os.environ['WIX_API_KEY'],
        'wix-site-id':   os.environ['WIX_SITE_ID'],
        'Content-Type':  'application/json'
    }


def fetch_all_wix_products():
    print("Fetching all products from Wix catalog...")
    sku_to_id = {}
    offset    = 0
    limit     = 100

    while True:
        body = {'query': {'paging': {'limit': limit, 'offset': offset}}}
        response = requests.post(
            f'{WIX_API_BASE}/products/query',
            headers=get_wix_headers(),
            json=body,
            timeout=15
        )

        if response.status_code != 200:
            print(f"  Wix catalog fetch failed: {response.status_code} {response.text}")
            break

        data     = response.json()
        products = data.get('products', [])

        for product in products:
            product_id = product.get('id')
            sku = product.get('sku', '').strip()
            if sku:
                sku_to_id[sku] = product_id
            for variant in product.get('variants', []):
                v_sku = variant.get('variant', {}).get('sku', '').strip()
                if v_sku:
                    sku_to_id[v_sku] = product_id

        print(f"  Fetched {offset + len(products)} products so far...")

        if len(products) < limit:
            break

        offset += limit

    print(f"  Total Wix products loaded: {len(sku_to_id)}\n")
    return sku_to_id


def update_wix_product(product_id, ribbon, description_html):
    url     = f'{WIX_API_BASE}/products/{product_id}'
    payload = {'product': {}}

    # Always send ribbon — empty string clears it
    payload['product']['ribbon'] = ribbon if ribbon else ''

    if description_html:
        payload['product']['additionalInfoSections'] = [
            {'title': 'Stock Status', 'description': description_html}
        ]

    try:
        response = requests.patch(url, headers=get_wix_headers(), json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"  Wix update error {product_id}: {e}")
        return False


def process_sku(sku, row, sku_to_id):
    try:
        combined_stock  = str(row.get('combined_stock', ''))
        combined_status = str(row.get('combined_status', ''))
        current_time    = row.get('last_updated', today)

        # Skip SKUs that haven't been scraped yet
        if pd.isna(row.get('combined_status')) or combined_status.strip() == '' or combined_status == 'nan':
            return sku, 'skipped'

        # Determine ribbon — never show ribbon for obsolete
        ribbon = 'Ships in 3-5 Days' if combined_stock == 'Active' and combined_status != 'Obsolete' else None

        # Determine status HTML
        if combined_status == 'Obsolete':
            status_html = obsoleteText
        elif combined_stock == 'Active':
            status_html = inStockText + f' as of {current_time}</strong></p>'
        else:
            status_html = outOfStockText

        # Look up Wix product ID
        product_id = sku_to_id.get(sku)

        # Try with leading zero after dash (e.g. 150275-1R5 → 150275-01R5)
        if not product_id:
            parts = sku.split('-')
            if len(parts) == 2 and not parts[1].startswith('0'):
                alt_sku = f"{parts[0]}-0{parts[1]}"
                product_id = sku_to_id.get(alt_sku)

        if not product_id:
            return sku, 'notfound'

        success = update_wix_product(product_id, ribbon, status_html)
        return sku, 'updated' if success else 'failed'

    except Exception as e:
        print(f"  Error on {sku}: {e}")
        return sku, 'failed'


def update_catalog():
    try:
        dfOutput = pd.read_csv('comp_data.csv', index_col='sku')
    except FileNotFoundError:
        raise SystemExit("Error: comp_data.csv not found.")

    print(f"Loaded {len(dfOutput)} SKUs from comp_data.csv")

    sku_to_id = fetch_all_wix_products()
    if not sku_to_id:
        raise SystemExit("Could not fetch Wix products. Check WIX_API_KEY and WIX_SITE_ID.")

    print(f"Starting parallel Wix updates...\n")
    results        = {'updated': 0, 'notfound': 0, 'skipped': 0, 'failed': 0}
    not_found_skus = []

    # Stock breakdown
    in_stock  = int((dfOutput['combined_stock'] == 'Active').sum()) if 'combined_stock' in dfOutput.columns else 0
    obsolete  = int((dfOutput['combined_status'] == 'Obsolete').sum()) if 'combined_status' in dfOutput.columns else 0
    out_stock = int(len(dfOutput) - in_stock - obsolete)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(process_sku, sku, dfOutput.loc[sku].to_dict(), sku_to_id): sku
            for sku in dfOutput.index
        }

        for i, fut in enumerate(as_completed(futures), 1):
            sku, result = fut.result()
            results[result] += 1

            if result == 'updated':
                print(f"  [{i}/{len(futures)}] ✓ {sku}")
            elif result == 'notfound':
                not_found_skus.append(sku)
                print(f"  [{i}/{len(futures)}] ✗ {sku} — not in Wix")
            elif result == 'failed':
                print(f"  [{i}/{len(futures)}] ! {sku} — update failed")

    print(f"\n── Update Complete ──")
    print(f"  Updated:   {results['updated']}")
    print(f"  Not found: {results['notfound']}")
    print(f"  Skipped:   {results['skipped']}")
    print(f"  Failed:    {results['failed']}")

    # Write summary
    scrape_summary = {}
    if os.path.exists('scrape_summary.json'):
        with open('scrape_summary.json') as f:
            scrape_summary = json.load(f)

    full_summary = {
        **scrape_summary,
        'wix_updated':    results['updated'],
        'wix_notfound':   results['notfound'],
        'wix_skipped':    results['skipped'],
        'wix_failed':     results['failed'],
        'in_stock':       in_stock,
        'out_of_stock':   out_stock,
        'obsolete':       obsolete,
        'not_found_skus': not_found_skus[:20],
    }

    with open('full_summary.json', 'w') as f:
        json.dump(full_summary, f)


if __name__ == '__main__':
    update_catalog()
