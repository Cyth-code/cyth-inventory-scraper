# -*- coding: utf-8 -*-
"""
apply_overrides.py — apply the CMS overrides only (no scraping).

  * cyth_inventory filled in → inStock = True, Active, "In Stock, Ready to Ship" ribbon
  * force_available_to_order ticked → inStock = False, Active, no ribbon
    ("Available to Order"); obsolete parts stay obsolete

Only those SKUs are touched. Takes a few minutes.

Env: WIX_API_KEY, WIX_SITE_ID
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import update as u


def main():
    df = pd.read_csv('comp_data.csv', dtype=str, index_col='sku')

    sku_to_item, cms_flagged = u.fetch_all_cms_items()
    df = u.apply_overrides(df, cms_flagged)

    targets = sorted(s for s in u.OVERRIDE_SKUS | u.CYTH_SKUS if s in df.index)
    if not targets:
        print('No SKUs are ticked or in Cyth inventory in the CMS. Nothing to do.')
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
    print('\nDone — Cyth inventory shows "In Stock, Ready to Ship"; ticked SKUs show "Available to Order".')


if __name__ == '__main__':
    main()
