# -*- coding: utf-8 -*-
"""
diagnose_store.py — Compare Wix Store API SKUs vs comp_data.csv

Run this once to identify which SKUs the Wix API is not returning.
Results saved to:
  - missing_from_api.txt  — SKUs in comp_data but not returned by Wix API
  - extra_in_api.txt      — SKUs returned by Wix API but not in comp_data
"""

import requests
import pandas as pd
import os
from pathlib import Path

script_dir = Path(__file__).resolve().parent
os.chdir(script_dir)

WIX_STORE_BASE = 'https://www.wixapis.com/stores/v1'

def get_wix_headers():
    return {
        'Authorization': os.environ['WIX_API_KEY'],
        'wix-site-id':   os.environ['WIX_SITE_ID'],
        'Content-Type':  'application/json'
    }

def fetch_all_store_skus():
    print("Fetching all SKUs from Wix Store API...")
    all_skus   = []
    blank_skus = 0
    offset     = 0
    limit      = 100

    while True:
        body = {'query': {'paging': {'limit': limit, 'offset': offset}}}
        response = requests.post(
            f'{WIX_STORE_BASE}/products/query',
            headers=get_wix_headers(),
            json=body,
            timeout=15
        )

        if response.status_code != 200:
            print(f"  Failed: {response.status_code} {response.text}")
            break

        products = response.json().get('products', [])

        for product in products:
            sku  = product.get('sku', '').strip()
            name = product.get('name', '')
            pid  = product.get('id', '')
            if sku:
                all_skus.append({'sku': sku, 'name': name, 'product_id': pid})
            else:
                blank_skus += 1
                print(f"  BLANK SKU: product_id={pid} name={name}")

        print(f"  Fetched {offset + len(products)} products...")
        if not products:
            break
        offset += limit

    print(f"\nTotal products fetched: {offset}")
    print(f"Products with SKU: {len(all_skus)}")
    print(f"Products with BLANK SKU: {blank_skus}")
    return all_skus, blank_skus


def main():
    # Fetch from API
    api_skus_list, blank_count = fetch_all_store_skus()
    api_skus = set(item['sku'] for item in api_skus_list)

    # Load comp_data
    comp_data  = pd.read_csv('comp_data.csv', dtype=str)
    comp_skus  = set(comp_data['sku'].dropna().str.strip().tolist())

    print(f"\n── Comparison ──")
    print(f"API SKUs:       {len(api_skus)}")
    print(f"Comp data SKUs: {len(comp_skus)}")

    # SKUs in comp_data but NOT returned by API
    missing = comp_skus - api_skus
    print(f"\nSKUs in comp_data NOT returned by API: {len(missing)}")
    with open('missing_from_api.txt', 'w') as f:
        for sku in sorted(missing):
            f.write(sku + '\n')
    print("  Saved to missing_from_api.txt")
    for sku in sorted(missing)[:20]:
        print(f"  - {sku}")
    if len(missing) > 20:
        print(f"  ... and {len(missing) - 20} more")

    # SKUs in API but NOT in comp_data
    extra = api_skus - comp_skus
    print(f"\nSKUs in API NOT in comp_data: {len(extra)}")
    with open('extra_in_api.txt', 'w') as f:
        for sku in sorted(extra):
            f.write(sku + '\n')
    print("  Saved to extra_in_api.txt")
    for sku in sorted(extra)[:20]:
        print(f"  + {sku}")

    print(f"\nProducts with blank SKU in API: {blank_count}")
    print("\nDone — check missing_from_api.txt and extra_in_api.txt in your repo")


if __name__ == '__main__':
    main()
