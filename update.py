# -*- coding: utf-8 -*-
"""
update.py — GitHub Actions compatible version

Changes from original:
  - Removed file backup logic (no persistent local filesystem in Actions)
  - Removed interactive input() prompts
  - Replaces fresh_import.csv output with direct Wix REST API calls
  - Wix credentials come from GitHub environment variables
"""

import pandas as pd
import datetime
import os
import requests
import time
from pathlib import Path

script_dir = Path(__file__).resolve().parent
os.chdir(script_dir)

# ── Status HTML (must match scrapeNetwork.py) ──────────────────────────────────
inStockText    = '<p style="color: #008000;"><strong>In-Stock Ready-to-Ship'
outOfStockText = '<p style="color: #000000;"><strong>Available to Order</strong></p> '
obsoleteText   = '<p style="color: #FF0000;"><strong>OBSOLETE - CONTACT CYTH</strong></p>'

today = datetime.datetime.now().strftime('%Y-%m-%d')

# ── Wix REST API ───────────────────────────────────────────────────────────────

WIX_API_BASE = 'https://www.wixapis.com/stores/v1'

def get_wix_headers():
    return {
        'Authorization': os.environ['WIX_API_KEY'],   # from GitHub Secrets
        'wix-site-id':   os.environ['WIX_SITE_ID'],   # from GitHub Secrets
        'Content-Type':  'application/json'
    }


def find_wix_product_by_sku(sku):
    url = f'{WIX_API_BASE}/products/query'
    body = {
        'query': {
            'filter': json.dumps({'sku': {'$eq': sku}}),
            'paging': {'limit': 1}
        }
    }
  
    response = requests.post(url, headers=get_wix_headers(), json=body)
    if response.status_code == 200:
        products = response.json().get('products', [])
        return products[0] if products else None
    print(f"  Wix query failed for SKU {sku}: {response.status_code} {response.text}")
    return None


def json_filter(field, value):
    return {field: {'$eq': value}}


def update_wix_product(product_id, ribbon, description_html):
    """
    PATCH a Wix product's ribbon and first additional info section.
    Only sends fields that need updating to avoid overwriting other data.
    """
    url = f'{WIX_API_BASE}/products/{product_id}'

    payload = {'product': {}}

    if ribbon:
        payload['product']['ribbon'] = ribbon

    if description_html:
        payload['product']['additionalInfoSections'] = [
            {
                'title': 'Stock Status',
                'description': description_html
            }
        ]

    response = requests.patch(url, headers=get_wix_headers(), json=payload)

    if response.status_code == 200:
        print(f"  ✓ Updated Wix product {product_id}")
        return True
    else:
        print(f"  ✗ Failed to update {product_id}: {response.status_code} {response.text}")
        return False


# ── Core Update Logic ──────────────────────────────────────────────────────────

def update_catalog():
    # Load comp_data (scraped from DigiKey/Newark)
    try:
        dfOutput = pd.read_csv('comp_data.csv', index_col='sku')
    except FileNotFoundError:
        raise SystemExit("Error: comp_data.csv not found. Run scrapeNetwork.py first.")

    print(f"Loaded comp_data.csv — {len(dfOutput)} SKUs")
    print(f"Starting Wix product updates at {today}\n")

    updated  = 0
    skipped  = 0
    notfound = 0

    for sku in dfOutput.index:
        print(f"Processing SKU: {sku}")

        if sku not in dfOutput.index or pd.isna(dfOutput.at[sku, 'combined_status']):
            print(f"  Skipping {sku} — no scraped data yet")
            skipped += 1
            continue

        currentTime    = dfOutput.at[sku, 'last_updated']
        combined_stock  = dfOutput.at[sku, 'combined_stock']
        combined_status = dfOutput.at[sku, 'combined_status']

        # ── Determine ribbon ──
        ribbon = 'Ships in 3-5 Days' if combined_stock == 'Active' else None

        # ── Determine status HTML ──
        if combined_status == 'Obsolete':
            status_html = obsoleteText
        elif ribbon == 'In Stock':
            status_html = inStockText + f' as of {currentTime}</strong></p>'
        else:
            status_html = outOfStockText

        # ── Find the product in Wix ──
        wix_product = find_wix_product_by_sku(sku)
        if not wix_product:
            print(f"  SKU {sku} not found in Wix catalog — skipping")
            notfound += 1
            continue

        product_id = wix_product['id']

        # ── Push update ──
        success = update_wix_product(product_id, ribbon, status_html)
        if success:
            updated += 1

        # Small delay to respect Wix rate limits
        time.sleep(0.2)

    print(f"\n── Update Complete ──")
    print(f"  Updated:   {updated}")
    print(f"  Not found: {notfound}")
    print(f"  Skipped:   {skipped}")


if __name__ == '__main__':
    update_catalog()
