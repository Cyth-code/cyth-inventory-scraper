# -*- coding: utf-8 -*-
"""
scrapeNetwork.py — Optimized parallel version

Optimization: One worker thread per DigiKey client.
With 6 clients = 6 parallel SKUs at a time = ~6x speedup.
Each worker owns its client exclusively so no rate limit conflicts.
"""

import requests
import pandas as pd
import datetime
import time
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


def newarkDF(sku):
    data = getNewarkJson(sku)
    cols = ['sku', 'newark_url', 'newark_inventory', 'newark_price', 'newark_status']

    if data and data['manufacturerPartNumberSearchReturn']['numberOfResults'] > 0:
        p = data['manufacturerPartNumberSearchReturn']['products'][0]
        row = {
            'sku':              sku,
            'newark_url':       p.get('productURL'),
            'newark_inventory': p.get('inv'),
            'newark_price':     p.get('prices', [{}])[0].get('cost'),
            'newark_status':    p.get('productStatus'),
        }
    else:
        row = {c: (sku if c == 'sku' else None) for c in cols}

    return pd.DataFrame({k: [v] for k, v in row.items()}).set_index('sku')

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
    row = {'sku': sku, 'digikey_url': None, 'digikey_inventory': None,
           'digikey_price': None, 'digikey_status': None}
    return pd.DataFrame({k: [v] for k, v in row.items()}).set_index('sku')


def getDigikeyRow(sku, client_id, access_token):
    response = getDigiKeyResponse(sku, client_id, access_token)

    if response is None:
        return empty_row(sku), False

    if response.status_code == 429:
        print(f"  DigiKey client {client_id[:8]} rate limited")
        return None, True  # exhausted

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

# ── Combine ────────────────────────────────────────────────────────────────────

def combineCols(df):
    # Only use DigiKey for status decisions
    dk_status = str(df.iloc[0].get('digikey_status', ''))
    dk_inventory = pd.to_numeric(df.iloc[0].get('digikey_inventory', 0), errors='coerce') or 0

    # Obsolete only if DigiKey says so
    is_obsolete = dk_status in ('Obsolete', 'Discontinued', '')

    df['combined_status'] = 'Obsolete' if is_obsolete else 'Active'

    # Stock only based on DigiKey inventory
    df['combined_stock'] = 'Active' if dk_inventory > 0 else 'Inactive'
    df['InStock'] = dk_inventory > 0
    df['last_updated'] = datetime.datetime.now().strftime('%m-%d-%Y')

# ── Worker ─────────────────────────────────────────────────────────────────────

write_lock = threading.Lock()

def process_sku(sku, client_id, access_token):
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=2) as ex:
            dk_fut = ex.submit(getDigikeyRow, sku, client_id, access_token)
            nw_fut = ex.submit(newarkDF, sku)
            dk_result, exhausted = dk_fut.result()
            nw_row = nw_fut.result()

        if dk_result is None:
            return sku, None, exhausted

        comp_row = dk_result.join(nw_row, how='outer')
        combineCols(comp_row)
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

        i += len(batch_skus)

        # Checkpoint every 100 SKUs
        if processed > 0 and processed % 100 == 0:
            with write_lock:
                comp_data.to_csv('comp_data.csv')
                print(f"  Checkpoint: {processed} done")

    # Final save
    comp_data.to_csv('comp_data.csv')
    next_index = (index + processed) % total
    with open('scrape_index.txt', 'w') as f:
        f.write(str(next_index))

    print(f"\nDone. Processed {processed} SKUs. Next run starts at index {next_index}.")


if __name__ == '__main__':
    main()
