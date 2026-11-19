# -*- coding: utf-8 -*-
"""
apply_overrides.py — apply "Available to Order" overrides only.

Reads SKUs ticked in the CMS checkbox (force_available_to_order), then for
just those SKUs:
  1. Sets inStock = False, combined_status = Active in the CMS
  2. Clears the store ribbon
No scraping, and no other SKUs are touched. Takes a minute or two.

Env: WIX_API_KEY, WIX_SITE_ID
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import update as u


def main():
    df = pd.read_csv('comp_data.csv', dtype=str, index_col='sku')

    sku_to_item, cms_flagged = u.fetch_all_cms_items()
    df = u.apply_overrides(df, cms_flagged)

    targets = sorted(s for s in u.OVERRIDE_SKUS if s in df.index)
    if not targets:
        print('No SKUs are ticked in the CMS. Nothing to do.')
        return

    print(f'Applying overrides to {len(targets)} SKU(s)\n')
    # Few SKUs: look each up directly. Many SKUs: one bulk store fetch is faster.
    sku_to_id = u.fetch_all_wix_products() if len(targets) > 50 else {}

    def apply(sku):
        row = df.loc[sku].to_dict()
        _, cms_result   = u.process_cms_sku(sku, row, sku_to_item)
        _, store_result = u.process_store_sku(sku, row, sku_to_id)
        return sku, cms_result, store_result

    failed = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in as_completed(ex.submit(apply, s) for s in targets):
            sku, cms_result, store_result = fut.result()
            if 'failed' in (cms_result, store_result) or store_result == 'notfound':
                failed.append(sku)
                print(f'  ! {sku}: CMS {cms_result}, ribbon {store_result}')
            elif len(targets) <= 50:
                print(f'  {sku}: CMS {cms_result}, ribbon {store_result}')
    print(f'\nProcessed {len(targets)} SKU(s), {len(failed)} with problems')

    if failed:
        raise SystemExit(f'\n{len(failed)} SKU(s) had problems: {", ".join(failed)}')
    print('\nDone — all flagged SKUs show "Available to Order".')


if __name__ == '__main__':
    main()
