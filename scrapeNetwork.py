# -*- coding: utf-8 -*-
"""
scrapeNetwork.py — GitHub Actions compatible version

Changes from original:
  - Removed interactive CLI menu (non-interactive environment)
  - Removed signal/SIGINT handler
  - Removed dotenv file dependency — secrets come from GitHub environment variables
  - INDEX is read/written to comp_data.csv metadata, not .env file
  - Exits cleanly when daily limit is hit (workflow commits comp_data.csv back to repo)
"""

import requests
import pandas as pd
import datetime
import time
import os
import json
import pprint
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
script_dir = Path(__file__).resolve().parent
os.chdir(script_dir)

# ── Status HTML strings (shared with update.py) ───────────────────────────────
inStockText   = '<p style="color: #008000;"><strong>In-Stock Ready-to-Ship'
outOfStockText = '<p style="color: #000000;"><strong>Available to Order</strong></p> '
obsoleteText  = '<p style="color: #FF0000;"><strong>OBSOLETE - CONTACT CYTH</strong></p>'

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
    response = requests.get(url, params=params)
    if response.status_code == 200:
        print('OK from Newark')
        return response.json()
    else:
        print(f"Newark Request failed with status code {response.status_code}")
        return None


def getNewarkRow(sku):
    data = getNewarkJson(sku)
    nwRowDict = {'sku': sku}

    if data and data['manufacturerPartNumberSearchReturn']['numberOfResults'] > 0:
        productData = data['manufacturerPartNumberSearchReturn']['products'][0]
        nwRowDict['newark_url']       = productData.get('productURL')
        nwRowDict['newark_inventory'] = productData.get('inv')
        nwRowDict['newark_price']     = productData.get('prices', [{}])[0].get('cost')
        nwRowDict['newark_status']    = productData.get('productStatus')

        nwRow = pd.DataFrame({k: [v] for k, v in nwRowDict.items()}).set_index('sku')
        return 1, nwRow
    return 0, None


def newarkDF(sku):
    nwResult, nwRow = getNewarkRow(sku)
    if nwResult == 0:
        nwRow = pd.DataFrame(
            [[sku, None, None, None, None]],
            columns=['sku', 'newark_url', 'newark_inventory', 'newark_price', 'newark_status']
        ).set_index('sku')
    return nwRow

# ── DigiKey API ────────────────────────────────────────────────────────────────

def getDigikeyAccess(client_id, client_secret):
    token_url = 'https://api.digikey.com/v1/oauth2/token'
    token_response = requests.post(
        token_url,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        data={'client_id': client_id, 'client_secret': client_secret, 'grant_type': 'client_credentials'}
    )
    if 'access_token' not in token_response.json():
        raise RuntimeError(f"Failed to get DigiKey access token: {token_response.text}")
    return token_response.json()['access_token']


def getDigiKeyResponse(sku, client_id, access_token):
    product_url = f'https://api.digikey.com/products/v4/search/{sku}/productdetails'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'X-DIGIKEY-Client-Id': client_id,
        'Accept': 'application/json'
    }
    try:
        response = requests.get(product_url, headers=headers, timeout=10)
        return response
    except requests.exceptions.ConnectTimeout:
        print(f"Timeout for SKU: {sku}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Request error for SKU {sku}: {e}")
        return None


def getDigikeyCredentials(sku, client_ids, dk_client_data):
    """
    Rotate through client IDs to find one that hasn't hit its daily limit.
    Raises RuntimeError if all clients are rate-limited (workflow should stop for the day).
    """
    for client_id in client_ids:
        client_secret = dk_client_data[client_id]
        access_token  = getDigikeyAccess(client_id, client_secret)
        response      = getDigiKeyResponse(sku, client_id, access_token)
        if response and response.status_code in (200, 404):
            print(f"Using client: {client_id[:6]}...")
            return client_id, access_token

    raise RuntimeError("All DigiKey clients have hit their daily rate limit. Stopping scrape.")


def empty_row(sku):
    row = {'sku': sku, 'digikey_url': None, 'digikey_inventory': None,
           'digikey_price': None, 'digikey_status': None}
    return pd.DataFrame({k: [v] for k, v in row.items()}).set_index('sku')


def getDigikeyRow(sku, creds, client_ids, dk_client_data):
    client_id, access_token = creds
    dk_response = getDigiKeyResponse(sku, client_id, access_token)

    if dk_response is None:
        return None, creds

    # Renew credentials if limit hit
    while dk_response.status_code not in (200, 404):
        print(f"Bad DigiKey response: {dk_response.status_code} — rotating client")
        creds = getDigikeyCredentials(sku, client_ids, dk_client_data)  # raises if all exhausted
        dk_response = getDigiKeyResponse(sku, *creds)

    print(f"OK from DigiKey: {dk_response.status_code}")

    if dk_response.status_code == 404:
        return empty_row(sku), creds

    productInfo = dk_response.json()['Product']
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
    dataDf = pd.DataFrame({k: [v] for k, v in productData.items()}).set_index('sku')
    return dataDf, creds

# ── Combination Logic ──────────────────────────────────────────────────────────

def combineCols(df):
    currentTime = datetime.datetime.now().strftime('%m-%d-%Y')

    df['combined_inventory'] = (
        pd.to_numeric(df['newark_inventory'], errors='coerce').fillna(0) +
        pd.to_numeric(df['digikey_inventory'], errors='coerce').fillna(0)
    )
    df['InStock'] = df['combined_inventory'] > 0

    dk_status = str(df.iloc[0].get('digikey_status', ''))
    nw_status = str(df.iloc[0].get('newark_status', ''))

    is_obsolete = (
        nw_status == 'NO_LONGER_MANUFACTURED' or
        dk_status == 'Obsolete' or
        (nw_status == '' and dk_status == '')
    )
    df['combined_status'] = 'Obsolete' if is_obsolete else 'Active'
    df['combined_stock']  = 'Active' if df.iloc[0]['InStock'] else 'Inactive'
    df['last_updated']    = currentTime
    return df

# ── Persistence ────────────────────────────────────────────────────────────────

def writeToCompData(comp_data, index):
    comp_data.to_csv('comp_data.csv')
    print(f'---> comp_data.csv written at index {index}')

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Load credentials from environment (set as GitHub Secrets)
    client_ids    = json.loads(os.environ['DK_CLIENT_ID'])
    dk_client_data = json.loads(os.environ['DL_CLIENT_DATA'])

    # Load data — comp_data.csv lives in the repo and persists between runs
    comp_data = pd.read_csv('comp_data.csv').set_index('sku')

    # INDEX: resume from where last run stopped (stored in env var set by workflow)
    index = int(os.environ.get('INDEX', '0'))
    print(f"Starting scrape from index {index} / {len(comp_data)}")

    # Get initial valid credentials
    probe_sku  = comp_data.index[index]
    credentials = getDigikeyCredentials(probe_sku, client_ids, dk_client_data)

    fail_count = 0

    while index < len(comp_data):
        sku = comp_data.index[index]
        print(f"[{index}/{len(comp_data)}] SKU: {sku}")

        try:
            result = getDigikeyRow(sku, credentials, client_ids, dk_client_data)
            if result[0] is None:
                index += 1
                continue
            dk_row, credentials = result

            nw_row   = newarkDF(sku)
            comp_row = dk_row.join(nw_row, how='outer')
            combineCols(comp_row)

            try:
                comp_data.loc[comp_row.index[0], comp_row.columns] = comp_row.iloc[0]
            except Exception as e:
                print(f"Row mismatch for {sku}: {e}")
                index += 1
                continue

            fail_count = 0
            index += 1

            # Checkpoint every 50 rows
            if index % 50 == 0:
                writeToCompData(comp_data, index)

        except RuntimeError as e:
            # All DigiKey clients exhausted — stop cleanly, workflow commits progress
            print(f"\n{e}")
            break

        except Exception as e:
            fail_count += 1
            print(f"Error on {sku} (attempt {fail_count}): {e}")
            if fail_count >= 5:
                print("5 consecutive failures — stopping scrape.")
                break
            time.sleep(min(30, 10 * fail_count))
            continue

    # Final write before exit
    writeToCompData(comp_data, index)

    # Output INDEX for the workflow to store in GitHub env
    # The workflow reads this file and sets it as an env var for next run
    with open('scrape_index.txt', 'w') as f:
        f.write(str(index % len(comp_data)))  # wrap around when complete

    print(f"\nScrape finished. Next run will start at index {index % len(comp_data)}.")


if __name__ == '__main__':
    main()
