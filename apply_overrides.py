# -*- coding: utf-8 -*-
"""
apply_overrides.py — apply "Available to Order" overrides only.

Reads SKUs ticked in the CMS checkbox (availableToOrder) plus any in
manual_skus.txt, then for just those SKUs:
  1. Sets inStock = False, combined_status = Active in the CMS
  2. Clears the store ribbon
No scraping, and no other SKUs are touched. Takes a minute or two.

Env: WIX_API_KEY, WIX_SITE_ID
"""

import pandas as pd
import update as u


def main():
    df = pd.read_csv('comp_data.csv', dtype=str, index_col='sku')

    sku_to_item, cms_flagged = u.fetch_all_cms_items()
    df = u.apply_manual_overrides(df, cms_flagged)

    targets = sorted(s for s in u.OVERRIDE_SKUS if s in df.index)
    if not targets:
        print('No SKUs are flagged for "Available to Order". Nothing to do.')
        return

    print(f'Applying overrides to {len(targets)} SKU(s)\n')
    failed = []
    for sku in targets:
        row = df.loc[sku].to_dict()
        _, cms_result   = u.process_cms_sku(sku, row, sku_to_item)
        # Empty map → uses targeted SKU query instead of fetching the whole store
        _, store_result = u.process_store_sku(sku, row, {})
        print(f'  {sku}: CMS {cms_result}, ribbon {store_result}')
        if 'failed' in (cms_result, store_result) or store_result == 'notfound':
            failed.append(sku)

    if failed:
        raise SystemExit(f'\n{len(failed)} SKU(s) had problems: {", ".join(failed)}')
    print('\nDone — all flagged SKUs show "Available to Order".')


if __name__ == '__main__':
    main()
