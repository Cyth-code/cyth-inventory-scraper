# -*- coding: utf-8 -*-
"""
send_report.py — email a summary of the weekly scraper run, with action items.

Reads full_summary.json (written by update.py). If the run failed before that
file was written, sends a short failure email instead.

Env:
  EMAIL_USERNAME  Gmail address the report is sent from
  EMAIL_PASSWORD  Gmail App Password (not the normal password)
  EMAIL_TO        recipient(s), comma-separated
  JOB_STATUS      success / failure (set by the workflow)
  RUN_URL         link to this workflow run (set by the workflow)
"""

import os
import json
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

MAX_LIST = 50   # SKUs shown per section; the rest are counted


def sku_list(skus):
    if not skus:
        return ''
    shown = ', '.join(escape(s) for s in skus[:MAX_LIST])
    more  = f' … and {len(skus) - MAX_LIST} more' if len(skus) > MAX_LIST else ''
    return f'<p style="font-family:monospace;font-size:13px">{shown}{more}</p>'


def section(title, skus, what_to_do):
    if not skus:
        return ''
    return (f'<h3 style="margin-bottom:4px">{escape(title)} ({len(skus)})</h3>'
            f'<p style="margin-top:0;color:#555">{escape(what_to_do)}</p>{sku_list(skus)}')


def build_email(summary, status, run_url):
    a = summary.get('actions', {})
    sections = ''.join([
        section('Newly obsolete', a.get('newly_obsolete', []),
                'Were Active last week, now obsolete at DigiKey and Newark. They show as obsolete on the site. '
                'Check whether any should be replaced or delisted.'),
        section('Ticked but obsolete', a.get('ticked_obsolete', []),
                'Ticked "Force Available to Order" but obsolete, so the site shows them as obsolete. '
                'Untick them in the CMS if they should stay that way.'),
        section('Back to Active, not ticked', a.get('back_active', []),
                'Were obsolete, now available again. Tick "Force Available to Order" in the CMS '
                'so they show as Available to Order.'),
        section('Showing "In Stock" ribbon, not ticked', a.get('ribbon_not_ticked', []),
                'Active and in stock, but not ticked, so they get the "In Stock, Ready to Ship" ribbon. '
                'Tick them in the CMS if they should show Available to Order instead.'),
        section('Ticked but not in the scraper data', a.get('ticked_missing', []),
                'Ticked in the CMS but not in comp_data.csv. Check the SKU spelling.'),
        section('Not found in Wix Store', a.get('not_in_store', []),
                'In the scraper data but no matching store product, so no ribbon was updated. '
                'Check the SKU on the store product.'),
    ])
    failures = a.get('cms_failed', 0) + a.get('store_failed', 0)
    if failures:
        sections += (f'<h3>Failed updates ({failures})</h3><p style="color:#555">'
                     f'{a.get("cms_failed", 0)} CMS and {a.get("store_failed", 0)} store updates failed. '
                     f'See the run log for details.</p>')
    if not sections:
        sections = '<p>✅ Nothing needs attention this week.</p>'

    headline = '✅ Completed' if status == 'success' else '⚠️ Finished with errors'
    totals = (f"In stock: {summary.get('in_stock', 0)} · Out of stock: {summary.get('out_of_stock', 0)} · "
              f"Obsolete: {summary.get('obsolete', 0)} · Ticked Available to Order: {a.get('ticked_total', 0)}<br>"
              f"CMS rows updated: {summary.get('cms_updated', 0)} · Store ribbons updated: {summary.get('wix_updated', 0)}")

    html = f'''<div style="font-family:Arial,sans-serif;font-size:14px;max-width:720px">
<h2 style="margin-bottom:4px">Inventory scraper run: {headline}</h2>
<p style="color:#555;margin-top:0">{date.today():%B %d, %Y}</p>
<p>{totals}</p>
<h2 style="border-top:1px solid #ddd;padding-top:12px">Action items</h2>
{sections}
<p style="border-top:1px solid #ddd;padding-top:12px"><a href="{escape(run_url)}">Open the full run log</a></p>
</div>'''
    action_count = sum(len(a.get(k, [])) for k in
                       ('newly_obsolete', 'ticked_obsolete', 'back_active', 'ribbon_not_ticked',
                        'ticked_missing', 'not_in_store'))
    subject = (f'Inventory scraper: {action_count} item(s) to review'
               if action_count or failures else 'Inventory scraper: no changes needed')
    if status != 'success':
        subject = '⚠️ ' + subject
    return subject, html


def build_failure_email(run_url):
    html = (f'<div style="font-family:Arial,sans-serif;font-size:14px">'
            f'<h2>⚠️ Inventory scraper run failed</h2>'
            f'<p>The run stopped before the Wix update finished, so there is no report this week.</p>'
            f'<p><a href="{escape(run_url)}">Open the run log</a> to see what went wrong.</p></div>')
    return '⚠️ Inventory scraper run failed', html


def main():
    user, password = os.environ.get('EMAIL_USERNAME'), os.environ.get('EMAIL_PASSWORD')
    recipients = [r.strip() for r in os.environ.get('EMAIL_TO', '').split(',') if r.strip()]
    if not (user and password and recipients):
        print('Email secrets not set (EMAIL_USERNAME, EMAIL_PASSWORD, EMAIL_TO) — skipping email.')
        return

    status  = os.environ.get('JOB_STATUS', 'success')
    run_url = os.environ.get('RUN_URL', '')

    if os.path.exists('full_summary.json'):
        with open('full_summary.json') as f:
            subject, html = build_email(json.load(f), status, run_url)
    else:
        subject, html = build_failure_email(run_url)

    msg = MIMEMultipart('alternative')
    msg['Subject'], msg['From'], msg['To'] = subject, user, ', '.join(recipients)
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as server:
        server.login(user, password)
        server.sendmail(user, recipients, msg.as_string())
    print(f'Report emailed to {", ".join(recipients)}: {subject}')


if __name__ == '__main__':
    main()
