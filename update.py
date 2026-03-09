# -*- coding: utf-8 -*-
"""
update.py — Optimized parallel version

Runs Wix product updates in parallel (10 at a time) instead of one by one.
"""

import pandas as pd
import datetime
import os
import json
import requests
import time
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


def find_wix_product_by_sku(sku):
    url  = f'{WIX_API_BASE}/products/query'
    body = {
        'query': {
            'filter': json.dumps({'sku': {'$eq': sku}}),
            'paging': {'limit': 1}
        }
    }
    try:
        response = requests.post(url, headers=get_wix_headers(), json=body, timeout=10)
        if response.status_code == 200:
            products = response.json().get('products', [])
            return products[0] if products else None
        print(f"  Wix query failed for {sku}: {response.status_code} {response.text}")
        return None
    except Exception as e:
        print(f"  Wix query error for {sku}: {e}")
        return None


def update_wix_product(product_id, ribbon, description_html):
    url     = f'{WIX_API_BASE}/products/{product_id}'
    payload = {'product': {}}

    if ribbon:
        payload['product']['ribbon'] = ribbon

    if description_html:
        payload['product']['additionalInfoSections'] = [
            {'title': 'Stock Status', 'description': description_html}
        ]

    try:
        response = requests.patch(url, headers=get_wix_headers(), json=payload, timeout=10)
        if response.status_code == 200:
            return True
        print(f"  Wix update failed {product_id}: {response.status_code} {response.text}")
        return False
    except Exception as e:
        print(f"  Wix update error {product_id}: {e}")
        return False


# ── Per-SKU worker ─────────────────────────────────────────────────────────────

def process_sku(sku, row):
    try:
        combined_stock  = row.get('combined_stock', '')
        combined_status = row.get('combined_status', '')
        current_time    = row.get('last_updated', today)

        if pd.isna(combined_status) or combined_status == '':
            return sku, 'skipped'

        ribbon = 'Ships in 3-5 Days' if combined_stock == 'Active' else None

        if combined_status == 'Obsolete':
            status_html = obsoleteText
        elif ribbon == 'In Stock':
            status_html = inStockText + f' as of {current_time}</strong></p>'
        else:
            status_html = outOfStockText

        wix_product = find_wix_product_by_sku(sku)
        if not wix_product:
            return sku, 'notfound'

        success = update_wix_product(wix_product['id'], ribbon, status_html)
        return sku, 'updated' if success else 'failed'

    except Exception as e:
        print(f"  Error on {sku}: {e}")
        return sku, 'failed'


# ── Main ───────────────────────────────────────────────────────────────────────

def update_catalog():
    try:
        dfOutput = pd.read_csv('comp_data.csv', index_col='sku')
    except FileNotFoundError:
        raise SystemExit("Error: comp_data.csv not found.")

    print(f"Loaded {len(dfOutput)} SKUs — starting parallel Wix updates\n")

    results = {'updated': 0, 'notfound': 0, 'skipped': 0, 'failed': 0}

    # Run 10 Wix updates at a time
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(process_sku, sku, dfOutput.loc[sku].to_dict()): sku
            for sku in dfOutput.index
        }

        for i, fut in enumerate(as_completed(futures), 1):
            sku, result = fut.result()
            results[result] += 1

            if result == 'updated':
                print(f"  [{i}/{len(futures)}] ✓ {sku}")
            elif result == 'notfound':
                print(f"  [{i}/{len(futures)}] ✗ {sku} — not in Wix")

    print(f"\n── Update Complete ──")
    print(f"  Updated:   {results['updated']}")
    print(f"  Not found: {results['notfound']}")
    print(f"  Skipped:   {results['skipped']}")
    print(f"  Failed:    {results['failed']}")


if __name__ == '__main__':
    update_catalog()
