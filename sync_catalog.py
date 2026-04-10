# -*- coding: utf-8 -*-
"""
sync_catalog.py — Syncs comp_data.csv with Wix catalog

Runs before scrapeNetwork.py on every run.
- Fetches all SKUs from Wix catalog
- Adds new SKUs to comp_data.csv (blank data rows)
- Removes SKUs no longer in Wix catalog
- Saves updated comp_data.csv
"""

import pandas as pd
import os
import json
import requests
from pathlib import Path

script_dir = Path(__file__).resolve().parent
os.chdir(script_dir)

WIX_API_BASE = 'https://www.wixapis.com/stores/v1'

def get_wix_headers():
    return {
        'Authorization': os.environ['WIX_API_KEY'],
        'wix-site-id':   os.environ['WIX_SITE_ID'],
        'Content-Type':  'application/json'
    }


def fetch_all_wix_skus():
    """Fetch all SKUs from Wix catalog."""
    print("Fetching all SKUs from Wix catalog...")
    wix_skus = {}
    offset   = 0
    limit    = 100

    while True:
        body = {'query': {'paging': {'limit': limit, 'offset': offset}}}
        response = requests.post(
            f'{WIX_API_BASE}/products/query',
            headers=get_wix_headers(),
            json=body,
            timeout=15
        )

        if response.status_code != 200:
            print(f"  Wix fetch failed: {response.status_code} {response.text}")
            break

        data     = response.json()
        products = data.get('products', [])

        for product in products:
            sku = product.get('sku', '').strip()
            if sku:
                wix_skus[sku] = product.get('name', '')
            # Also check variants
            for variant in product.get('variants', []):
                v_sku = variant.get('variant', {}).get('sku', '').strip()
                if v_sku:
                    wix_skus[v_sku] = product.get('name', '')

        print(f"  Fetched {offset + len(products)} products...")

        if len(products) < limit:
            break

        offset += limit

    print(f"  Total Wix SKUs: {len(wix_skus)}\n")
    return wix_skus


def sync_catalog():
    # Load existing comp_data
    try:
        comp_data = pd.read_csv('comp_data.csv').set_index('sku')
        print(f"Loaded comp_data.csv — {len(comp_data)} existing SKUs")
    except FileNotFoundError:
        print("comp_data.csv not found — creating fresh one")
        comp_data = pd.DataFrame()

    # Fetch all Wix SKUs
    wix_skus = fetch_all_wix_skus()

    if not wix_skus:
        print("Could not fetch Wix SKUs — skipping sync")
        return

    wix_sku_set  = set(wix_skus.keys())
    comp_sku_set = set(comp_data.index) if len(comp_data) > 0 else set()

    # Find new SKUs to add
    new_skus = wix_sku_set - comp_sku_set
    # Find SKUs to remove (in comp_data but not in Wix)
    removed_skus = comp_sku_set - wix_sku_set

    print(f"New SKUs to add:     {len(new_skus)}")
    print(f"SKUs to remove:      {len(removed_skus)}")
    print(f"SKUs unchanged:      {len(comp_sku_set & wix_sku_set)}\n")

    # Add new SKUs with blank data
    if new_skus:
        blank_cols = [
            'digikey_url', 'digikey_inventory', 'digikey_price', 'digikey_status',
            'newark_url', 'newark_inventory', 'newark_price', 'newark_status',
            'combined_inventory', 'InStock', 'combined_status', 'last_updated', 'combined_stock'
        ]
        # Make sure comp_data has all columns
        for col in blank_cols:
            if col not in comp_data.columns:
                comp_data[col] = None

        new_rows = pd.DataFrame(
            index=list(new_skus),
            columns=comp_data.columns
        )
        new_rows.index.name = 'sku'
        comp_data = pd.concat([comp_data, new_rows])
        print(f"Added {len(new_skus)} new SKUs:")
        for sku in sorted(new_skus):
            print(f"  + {sku} ({wix_skus[sku]})")

    # Remove SKUs no longer in Wix
    if removed_skus:
        comp_data = comp_data.drop(index=list(removed_skus))
        print(f"\nRemoved {len(removed_skus)} SKUs no longer in Wix:")
        for sku in sorted(removed_skus):
            print(f"  - {sku}")

    # Save updated comp_data
    comp_data.to_csv('comp_data.csv')
    print(f"\nSync complete — comp_data.csv now has {len(comp_data)} SKUs")


if __name__ == '__main__':
    sync_catalog()
