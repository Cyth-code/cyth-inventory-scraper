# -*- coding: utf-8 -*-
"""
update.py — Pushes scraped data directly to Wix CMS collection

Instead of updating product ribbons, this version:
  1. Reads comp_data.csv
  2. Fetches all existing items from the CMS collection (by SKU)
  3. Updates existing items or creates new ones
  4. Runs 10 updates in parallel
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

today = datetime.datetime.now().strftime('%Y-%m-%d')

# ── Wix CMS API ────────────────────────────────────────────────────────────────
WIX_CMS_BASE   = 'https://www.wixapis.com/wix-data/v2/items'
COLLECTION_ID  = 'Import912'

def get_wix_headers():
    return {
        'Authorization': os.environ['WIX_API_KEY'],
        'wix-site-id':   os.environ['WIX_SITE_ID'],
        'Content-Type':  'application/json'
    }


def fetch_all_cms_items():
    """Fetch all existing items from CMS collection, keyed by SKU."""
    print("Fetching existing CMS items...")
    sku_to_item = {}
    offset      = 0
    limit       = 100

    while True:
        body = {
            'dataCollectionId': COLLECTION_ID,
            'query': {
                'paging': {'limit': limit, 'offset': offset}
            }
        }
        response = requests.post(
            f'https://www.wixapis.com/wix-data/v2/items/query',
            headers=get_wix_headers(),
            json=body,
            timeout=15
        )

        if response.status_code != 200:
            print(f"  CMS fetch failed: {response.status_code} {response.text}")
            break

        data  = response.json()
        items = data.get('dataItems', [])

        for item in items:
            sku = item.get('data', {}).get('sku', '').strip()
            if sku:
                sku_to_item[sku] = item.get('id')

        print(f"  Fetched {offset + len(items)} items...")

        if not items:
            break

        offset += limit

    print(f"  Total CMS items: {len(sku_to_item)}\n")
    return sku_to_item


def update_cms_item(item_id, data):
    """Update an existing CMS item."""
    body = {
        'dataCollectionId': COLLECTION_ID,
        'dataItem': {
            'id':   item_id,
            'data': data
        }
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
    """Create a new CMS item."""
    body = {
        'dataCollectionId': COLLECTION_ID,
        'dataItem': {
            'data': data
        }
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


def process_sku(sku, row, sku_to_item):
    """Update or create a CMS item for this SKU."""
    try:
        combined_status = str(row.get('combined_status', ''))
        combined_stock  = str(row.get('combined_stock', ''))

        # Skip unscraped SKUs
        if pd.isna(row.get('combined_status')) or combined_status in ('', 'nan'):
            return sku, 'skipped'

        # Build CMS data payload
        data = {
            'sku':                sku,
            'digikey_url':        str(row.get('digikey_url', '') or ''),
            'digikey_inventory':  float(row.get('digikey_inventory', 0) or 0),
            'digikey_price':      float(row.get('digikey_price', 0) or 0),
            'digikey_status':     str(row.get('digikey_status', '') or ''),
            'newark_url':         str(row.get('newark_url', '') or ''),
            'newark_inventory':   float(row.get('newark_inventory', 0) or 0),
            'newark_price':       float(row.get('newark_price', 0) or 0),
            'newark_status':      str(row.get('newark_status', '') or ''),
            'combined_inventory': float(row.get('combined_inventory', 0) or 0),
            'InStock':            bool(row.get('InStock', False)),
            'combined_status':    combined_status,
            'last_updated':       str(row.get('last_updated', today) or today),
        }

        item_id = sku_to_item.get(sku)

        if item_id:
            success = update_cms_item(item_id, data)
            return sku, 'updated' if success else 'failed'
        else:
            success = create_cms_item(data)
            return sku, 'created' if success else 'failed'

    except Exception as e:
        print(f"  Error on {sku}: {e}")
        return sku, 'failed'


def update_catalog():
    try:
        dfOutput = pd.read_csv('comp_data.csv', index_col='sku')
    except FileNotFoundError:
        raise SystemExit("Error: comp_data.csv not found.")

    print(f"Loaded {len(dfOutput)} SKUs from comp_data.csv")

    # Fetch existing CMS items
    sku_to_item = fetch_all_cms_items()

    print(f"Starting parallel CMS updates...\n")
    results = {'updated': 0, 'created': 0, 'skipped': 0, 'failed': 0}

    # Stock breakdown
    in_stock = int((dfOutput['combined_stock'] == 'Active').sum()) if 'combined_stock' in dfOutput.columns else 0
    obsolete = int((dfOutput['combined_status'] == 'Obsolete').sum()) if 'combined_status' in dfOutput.columns else 0
    out_stock = int(len(dfOutput) - in_stock - obsolete)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(process_sku, sku, dfOutput.loc[sku].to_dict(), sku_to_item): sku
            for sku in dfOutput.index
        }

        for i, fut in enumerate(as_completed(futures), 1):
            sku, result = fut.result()
            results[result] += 1

            if result in ('updated', 'created'):
                print(f"  [{i}/{len(futures)}] ✓ {sku} ({result})")
            elif result == 'failed':
                print(f"  [{i}/{len(futures)}] ! {sku} — failed")

    print(f"\n── CMS Update Complete ──")
    print(f"  Updated:  {results['updated']}")
    print(f"  Created:  {results['created']}")
    print(f"  Skipped:  {results['skipped']}")
    print(f"  Failed:   {results['failed']}")

    # Write summary
    scrape_summary = {}
    if os.path.exists('scrape_summary.json'):
        with open('scrape_summary.json') as f:
            scrape_summary = json.load(f)

    full_summary = {
        **scrape_summary,
        'wix_updated':  results['updated'],
        'wix_created':  results['created'],
        'wix_skipped':  results['skipped'],
        'wix_failed':   results['failed'],
        'in_stock':     in_stock,
        'out_of_stock': out_stock,
        'obsolete':     obsolete,
    }

    with open('full_summary.json', 'w') as f:
        json.dump(full_summary, f)


if __name__ == '__main__':
    update_catalog()
