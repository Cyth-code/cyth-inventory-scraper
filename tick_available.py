# -*- coding: utf-8 -*-
"""
tick_available.py — one-time bulk update: tick force_available_to_order for
every CMS product that is available at DigiKey or Newark (combined_status = Active).

Obsolete / not-found products are left unticked. Products already ticked are
left alone. Nothing is ever unticked. All other fields on each row are kept.

Run with DRY_RUN=yes first (default) to see counts, then DRY_RUN=no to apply.
Afterwards, run "Apply 'Available to Order' Overrides" to update the site.

Env: WIX_API_KEY, WIX_SITE_ID, DRY_RUN (yes/no)
"""

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import update as u

DRY_RUN = os.environ.get('DRY_RUN', 'yes').strip().lower() != 'no'


def tick(sku, item_id):
    data = {**u.editable_fields(u.CMS_DATA[sku]), u.OVERRIDE_FIELD: True}
    return sku, u.update_cms_item(item_id, data)


def main():
    print(f"Mode: {'DRY RUN (no changes)' if DRY_RUN else 'LIVE — ticking products'}\n")
    sku_to_item, already = u.fetch_all_cms_items()

    statuses = Counter()
    to_tick, ticked_not_active = [], []
    for sku, item_id in sku_to_item.items():
        status = (u.CMS_DATA[sku].get('combined_status') or '').strip() or '(blank / not found)'
        statuses[status] += 1
        if status == 'Active' and sku not in already:
            to_tick.append((sku, item_id))
        elif status != 'Active' and sku in already:
            ticked_not_active.append((sku, status))

    print('CMS products by combined_status:')
    for status, n in statuses.most_common():
        print(f'  {n:>5}  {status}')
    print(f'\nAlready ticked:  {len(already)}')
    print(f'Will be ticked:  {len(to_tick)}  (Active and not yet ticked)')
    if ticked_not_active:
        print(f'\n⚠️ {len(ticked_not_active)} ticked product(s) are NOT Active — review in CMS:')
        for sku, status in sorted(ticked_not_active):
            print(f'    {sku}  ({status})')

    if DRY_RUN:
        print('\nDry run — nothing changed. Re-run with dry_run = no to apply.')
        return

    ok, failed = 0, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in as_completed(ex.submit(tick, s, i) for s, i in to_tick):
            sku, success = fut.result()
            if success:
                ok += 1
            else:
                failed.append(sku)
    print(f'\nTicked {ok} product(s), {len(failed)} failed')
    if failed:
        raise SystemExit('Failed: ' + ', '.join(sorted(failed)))
    print('Next: run "Apply \'Available to Order\' Overrides" to update the site.')


if __name__ == '__main__':
    main()
