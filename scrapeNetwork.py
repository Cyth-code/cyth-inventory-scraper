# -*- coding: utf-8 -*-
"""
scrapeNetwork.py — DigiKey-first logic, weekly run

Logic:
  1. Check DigiKey first
     - In stock → Ships in 3-5 Days, skip Newark
     - Out of stock or Obsolete → Check Newark as fallback
  2. Newark fallback:
     - Newark has stock → Ships in 3-5 Days
     - Neither has stock but active → Available to Order
     - Both obsolete → OBSOLETE - CONTACT CYTH

Runs weekly on Monday at midnight Pacific (7 AM UTC).
"""

import requests
import pandas as pd
import datetime
import os
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Paths ──────────────────────────────────────────────────────────────────────
script_dir = Path(__file__).resolve().parent
os.chdir(script_dir)

# ── Newark API ─────────────────────────────────────────────────────────────────

def getNewarkJson(sku):
    API_KEY = os.environ['NW_API_KEY']
    url = 'https://api.element14.com/catalog/products'
    params = {
        'versionNumber': '1.3',
        'term': f'manuPartNum:{sku}',
        'storeInfo.id': 'www.newark.com',
        'resultsSettings.responseGroup': 'large',
        'callInfo.responseDataFormat': 'json',
        'callInfo.apiKey': API_KEY
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"  Newark {sku}: status {response.status_code}")
            return None
    except Exception as e:
        print(f"  Newark {sku} error: {e}")
        return None


def getNewarkData(sku):
    """Returns (inventory, status) from Newark."""
    data = getNewarkJson(sku)
    if data and data['manufacturerPartNumberSearchReturn']['numberOfResults'] > 0:
        p = data['manufacturerPartNumberSearchReturn']['products'][0]
        inventory = p.get('inv', 0) or 0
        status    = p.get('productStatus', '')
        return int(inventory), status
    return 0, ''

# ── DigiKey API ────────────────────────────────────────────────────────────────

def getDigikeyAccess(client_id, client_secret):
    response = requests.post(
        'https://api.digikey.com/v1/oauth2/token',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        data={'client_id': client_id, 'client_secret': client_secret, 'grant_type': 'client_credentials'}
    )
    data = response.json()
    if 'access_token' not in data:
        raise RuntimeError(f"Token fetch failed for {client_id[:8]}: {response.text}")
    return data['access_token']


def getDigiKeyResponse(sku, client_id, access_token):
    try:
        response = requests.get(
            f'https://api.digikey.com/products/v4/search/{sku}/productdetails',
            headers={
                'Authorization': f'Bearer {access_token}',
                'X-DIGIKEY-Client-Id': client_id,
                'Accept': 'application/json'
            },
            timeout=10
        )
        return response
    except Exception as e:
        print(f"  DigiKey request error for {sku}: {e}")
        return None


def empty_row(sku):
    row = {'sku': sku, 'digikey_url': None, 'digikey_inventory': 0,
           'digikey_price': None, 'digikey_status': ''}
    return pd.DataFrame({k: [v] for k, v in row.items()}).set_index('sku')


def getDigikeyRow(sku, client_id, access_token):
    response = getDigiKeyResponse(sku, client_id, access_token)

    if response is None:
        return empty_row(sku), False

    if response.status_code == 429:
        print(f"  DigiKey client {client_id[:8]} rate limited")
        return None, True  # exhausted

    if response.status_code == 401:
        print(f"  DigiKey 401 for {sku} — refreshing token")
        try:
            with open('clients.json') as f:
                creds = json.load(f)
            new_token = getDigikeyAccess(client_id, creds['client_data'][client_id])
            response  = getDigiKeyResponse(sku, client_id, new_token)
            if response and response.status_code == 200:
                return getDigikeyRow(sku, client_id, new_token)
        except Exception as e:
            print(f"  Token refresh failed: {e}")
        return empty_row(sku), False

    if response.status_code == 404:
        return empty_row(sku), False

    if response.status_code != 200:
        print(f"  DigiKey unexpected {response.status_code} for {sku}")
        return empty_row(sku), False

    print(f"  OK DigiKey: {sku}")
    productInfo = response.json()['Product']
    productData = {
        'sku':               sku,
        'digikey_url':       productInfo['ProductUrl'],
        'digikey_inventory': productInfo['QuantityAvailable'],
        'digikey_status':    productInfo['ProductStatus']['Status'],
        'digikey_price':     (
            productInfo['ProductVariations'][0]['StandardPricing'][0]['UnitPrice']
            if productInfo['ProductVariations'][0]['StandardPricing'] else 0
        ),
    }
    return pd.DataFrame({k: [v] for k, v in productData.items()}).set_index('sku'), False

# ── DigiKey-first Logic ────────────────────────────────────────────────────────

def buildCompRow(sku, dk_row):
    """
    DigiKey-first logic:
    1. DigiKey in stock → Active, skip Newark
    2. DigiKey out of stock/obsolete → check Newark
    3. Newark in stock → Active (Ships in 3-5 Days)
    4. Neither in stock but active → Available to Order
    5. Both obsolete → Obsolete
    """
    current_time = datetime.datetime.now().strftime('%m-%d-%Y')

    dk_inventory = int(pd.to_numeric(dk_row.iloc[0].get('digikey_inventory', 0), errors='coerce') or 0)
    dk_status    = str(dk_row.iloc[0].get('digikey_status', ''))
    dk_obsolete  = dk_status in ('Obsolete', 'Discontinued', '')
    dk_in_stock  = dk_inventory > 0

    # ── Step 1: DigiKey in stock → skip Newark ──
    if dk_in_stock and not dk_obsolete:
        print(f"  {sku}: DigiKey in stock ({dk_inventory}) — skipping Newark")
        dk_row['newark_inventory'] = 0
        dk_row['newark_status']    = ''
        dk_row['newark_url']       = None
        dk_row['newark_price']     = None
        dk_row['combined_inventory'] = dk_inventory
        dk_row['InStock']          = True
        dk_row['combined_stock']   = 'Active'
        dk_row['combined_status']  = 'Active'
        dk_row['last_updated']     = current_time
        return dk_row

    # ── Step 2: DigiKey out of stock/obsolete → check Newark ──
    print(f"  {sku}: DigiKey no stock — checking Newark")
    nw_inventory, nw_status = getNewarkData(sku)
    nw_obsolete = nw_status in ('NO_LONGER_MANUFACTURED', 'NO_LONGER_STOCKED', '')
    nw_in_stock = nw_inventory > 0

    dk_row['newark_inventory'] = nw_inventory
    dk_row['newark_status']    = nw_status
    dk_row['newark_url']       = None
    dk_row['newark_price']     = None

    combined_inventory = dk_inventory + nw_inventory

    # ── Step 3: Final status ──
    if nw_in_stock and not nw_obsolete:
        # Newark has stock → can resell → Ships in 3-5 Days
        combined_status = 'Active'
        combined_stock  = 'Active'
    elif not dk_obsolete or not nw_obsolete:
        # At least one still active but no stock → Available to Order
        combined_status = 'Active'
        combined_stock  = 'Inactive'
    else:
        # Both obsolete → Obsolete
        combined_status = 'Obsolete'
        combined_stock  = 'Inactive'

    dk_row['combined_inventory'] = combined_inventory
    dk_row['InStock']            = combined_inventory > 0
    dk_row['combined_stock']     = combined_stock
    dk_row['combined_status']    = combined_status
    dk_row['last_updated']       = current_time

    return dk_row

# ── Worker ─────────────────────────────────────────────────────────────────────

write_lock = threading.Lock()

def process_sku(sku, client_id, access_token):
    try:
        dk_result, exhausted = getDigikeyRow(sku, client_id, access_token)

        if dk_result is None:
            return sku, None, exhausted

        comp_row = buildCompRow(sku, dk_result)
        return sku, comp_row, exhausted

    except Exception as e:
        print(f"  Error processing {sku}: {e}")
        return sku, None, False

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    with open('clients.json') as f:
        creds_file = json.load(f)
    client_ids     = creds_file['client_ids']
    dk_client_data = creds_file['client_data']

    comp_data = pd.read_csv('comp_data.csv').set_index('sku')
    total     = len(comp_data)

    index = int(os.environ.get('INDEX', '0'))
    if index >= total:
        index = 0
    print(f"Starting at index {index} / {total} — {len(client_ids)} parallel workers")

    # Get fresh tokens for all clients
    client_tokens = {}
    for cid in client_ids:
        try:
            token = getDigikeyAccess(cid, dk_client_data[cid])
            client_tokens[cid] = token
            print(f"Token OK: {cid[:8]}...")
        except Exception as e:
            print(f"Token FAILED for {cid[:8]}: {e}")

    active_clients = list(client_tokens.keys())
    if not active_clients:
        raise RuntimeError("No valid DigiKey clients available.")

    skus          = list(comp_data.index[index:])
    processed     = 0
    failed        = 0
    exhausted_set = set()

    print(f"Processing {len(skus)} SKUs with {len(active_clients)} workers\n")

    i = 0
    while i < len(skus):
        available = [c for c in active_clients if c not in exhausted_set]
        if not available:
            print("All DigiKey clients exhausted. Stopping.")
            break

        batch_skus    = skus[i:i + len(available)]
        batch_clients = available[:len(batch_skus)]

        with ThreadPoolExecutor(max_workers=len(batch_skus)) as executor:
            futures = {
                executor.submit(process_sku, sku, cid, client_tokens[cid]): cid
                for sku, cid in zip(batch_skus, batch_clients)
            }

            for fut in as_completed(futures):
                sku, comp_row, exhausted = fut.result()
                cid = futures[fut]

                if exhausted:
                    exhausted_set.add(cid)
                    print(f"Client {cid[:8]} exhausted")

                if comp_row is not None:
                    with write_lock:
                        try:
                            comp_data.loc[comp_row.index[0], comp_row.columns] = comp_row.iloc[0]
                            processed += 1
                        except Exception as e:
                            print(f"  Row write error for {sku}: {e}")
                            failed += 1
                else:
                    failed += 1

        i += len(batch_skus)

        if processed > 0 and processed % 100 == 0:
            with write_lock:
                comp_data.to_csv('comp_data.csv')
                print(f"  Checkpoint: {processed} done")

    # Final save
    comp_data.to_csv('comp_data.csv')
    next_index = (index + processed) % total

    with open('scrape_index.txt', 'w') as f:
        f.write(str(next_index))

    # Write summary
    summary = {
        'run_date':          datetime.datetime.now().strftime('%m/%d/%Y %I:%M %p UTC'),
        'status':            'Success' if processed > 0 else 'Failed',
        'skus_processed':    processed,
        'total_skus':        total,
        'failed_skus':       failed,
        'next_index':        next_index,
        'clients_total':     len(active_clients),
        'clients_exhausted': len(exhausted_set),
        'clients_remaining': len(active_clients) - len(exhausted_set),
    }

    with open('scrape_summary.json', 'w') as f:
        json.dump(summary, f)

    print(f"\nDone. Processed {processed} SKUs. Next run starts at index {next_index}.")


if __name__ == '__main__':
    main()
