# Cyth Systems — Inventory Scraper

Automated weekly inventory scraping and Wix store update pipeline. Pulls stock and pricing data from DigiKey and Newark APIs, updates a Wix CMS collection, and sets product ribbons on the Wix Store catalog — all without any manual steps.

---

## How It Works

```
Every Wednesday at midnight Pacific (7 AM UTC)
            ↓
GitHub Actions triggers the workflow
            ↓
scrapeNetwork.py
  → Calls DigiKey API for each SKU (6 parallel workers)
  → If DigiKey has no stock → calls Newark as fallback
  → Saves results to comp_data.csv
  → Commits progress back to repo
            ↓
update.py
  → Step 1: Pushes all data to Wix CMS collection (Products - Partner Inventory)
  → Step 2: Updates product ribbons in Wix Store catalog
            ↓
Website automatically reflects updated stock status and ribbons
```

---

## Stock Logic

| DigiKey | Newark | Result |
|---|---|---|
| In stock | — (skipped) | **Ships in 3-5 Days** ribbon, InStock = true |
| No stock | In stock | **Ships in 3-5 Days** ribbon, InStock = true |
| No stock | No stock | No ribbon, **Available to Order** |
| Obsolete | Obsolete | No ribbon, **OBSOLETE - CONTACT CYTH** |
| Not found | Not found | No ribbon, **OBSOLETE - CONTACT CYTH** |

---

## Repository Structure

```
cyth-inventory-scraper/
├── scrapeNetwork.py          # DigiKey + Newark API scraper
├── update.py                 # Wix CMS + Store ribbon updater
├── comp_data.csv             # SKU database (source of truth)
├── scrape_index.txt          # Resume position for next run
├── clients.json              # DigiKey client credentials
├── not_in_store.txt          # SKUs not found in Wix Store (generated each run)
├── scrape_summary.json       # Last scrape stats (generated each run)
├── full_summary.json         # Last full run stats (generated each run)
├── diagnose_store.py         # Diagnostic tool for Wix API discrepancies
└── .github/
    └── workflows/
        └── daily_scrape.yml  # GitHub Actions workflow
```

---

## File Reference

### `comp_data.csv`
The persistent SKU database. Every SKU in the Wix store catalog is listed here. The scraper reads SKUs from this file and writes back updated inventory data after each run.

**Columns:**
| Column | Description |
|---|---|
| `sku` | Product SKU (index) |
| `digikey_url` | DigiKey product URL |
| `digikey_inventory` | DigiKey stock quantity |
| `digikey_price` | DigiKey unit price |
| `digikey_status` | DigiKey lifecycle status (Active/Obsolete/Discontinued) |
| `newark_url` | Newark product URL |
| `newark_inventory` | Newark stock quantity |
| `newark_price` | Newark unit price |
| `newark_status` | Newark lifecycle status |
| `combined_inventory` | Total stock across both sources |
| `InStock` | True/False string |
| `combined_status` | Active or Obsolete |
| `combined_stock` | Active (has stock) or Inactive (no stock) |
| `last_updated` | Date last scraped (MM-DD-YYYY) |

> ⚠️ Do not edit `comp_data.csv` manually. It is updated automatically by the scraper every run.

### `clients.json`
Stores DigiKey API client credentials. Structure:
```json
{
  "client_ids": ["id1", "id2", "..."],
  "client_data": {
    "id1": "secret1",
    "id2": "secret2"
  }
}
```
> ⚠️ This file contains sensitive credentials. The repo must remain **private**.

### `scrape_index.txt`
Tracks where the last scrape stopped. On the next run, the scraper resumes from this index. Automatically updated after every run.

### `not_in_store.txt`
Generated after each update run. Lists any SKUs that exist in `comp_data.csv` but could not be matched to a Wix Store product. Review these to identify products that may need attention in the Wix catalog.

---

## GitHub Actions Workflow

The workflow runs every **Wednesday at midnight Pacific Time (7 AM UTC)**.

### Steps
1. **Checkout repository** — pulls latest `comp_data.csv` and `scrape_index.txt`
2. **Set scrape index** — resumes from saved index or override
3. **Run scrapeNetwork.py** — calls DigiKey + Newark APIs
4. **Run update.py** — pushes data to Wix CMS and Store
5. **Commit updated files** — saves `comp_data.csv`, `scrape_index.txt`, `not_in_store.txt` back to repo

### Manual Trigger
Go to **Actions → Weekly Product Scrape & Wix Update → Run workflow**

Options:
- **start_index** — override the starting SKU index (leave blank to resume from saved position)
- **skip_scrape** — set to `yes` to skip scraping and only run the Wix update step

---

## GitHub Secrets

All credentials are stored as encrypted GitHub Secrets. Go to **Settings → Secrets and variables → Actions** to manage them.

| Secret | Description | Where to get it |
|---|---|---|
| `NW_API_KEY` | Newark / Element14 API key | developer.element14.com |
| `WIX_API_KEY` | Wix API key (Stores + CMS read/write) | manage.wix.com/account/api-keys |
| `WIX_SITE_ID` | Wix site ID | From dashboard URL: `manage.wix.com/dashboard/{SITE_ID}/home` |
| `EMAIL_USERNAME` | Gmail address for reports (optional) | Gmail account |
| `EMAIL_PASSWORD` | Gmail App Password for reports (optional) | myaccount.google.com/apppasswords |

> Note: DigiKey credentials are stored in `clients.json` in the repo (not as secrets) due to the JSON size limit of GitHub Secrets.

---

## Wix Integration

### CMS Collection — `Products - Partner Inventory` (ID: `Import912`)
Updated by `update.py` Step 1. All scraped fields are pushed here. The website reads stock status, dates, URLs, and pricing from this collection.

**Key fields:**
- `sku` — product identifier
- `inStock` — boolean checkbox (ticked if DigiKey or Newark has stock)
- `combined_status` — `Active` or `Obsolete`
- `last_updated` — date of last scrape
- `digikey_url` / `newark_url` — direct links to product pages

### Wix Store — Product Ribbons
Updated by `update.py` Step 2. Sets the ribbon badge on each product card in the catalog.

| Stock Status | Ribbon |
|---|---|
| In stock (DigiKey or Newark) | `Ships in 3-5 Days` |
| Out of stock | *(blank)* |
| Obsolete | *(blank)* |

---

## DigiKey Rate Limits

DigiKey allows **1,000 API requests per client per day**. With 6 clients configured in `clients.json`, the daily capacity is **6,000 requests** — enough to cover all 3,425 SKUs in one run.

If all clients are exhausted mid-run, the scraper stops cleanly and saves progress. The next Wednesday's run resumes from where it left off.

---

## Newark API

Newark (Element14) is used as a **fallback only** — it is only called for SKUs where DigiKey shows no stock or obsolete status. This significantly reduces Newark API usage compared to calling it for every SKU.

> ⚠️ Newark has a monthly API request limit. If the limit is hit, the scraper handles 403 responses gracefully and continues using DigiKey data only.

---

## Switching to a New Website

To run this scraper on a different Wix site:

1. Get the new site's ID from the dashboard URL
2. Update the `WIX_SITE_ID` secret in GitHub
3. Generate a new API key at manage.wix.com/account/api-keys with Wix Stores + CMS permissions
4. Update the `WIX_API_KEY` secret

The scraper will automatically target the new site on the next run. No code changes required.

To run on **both sites simultaneously**, create a second workflow file `.github/workflows/daily_scrape_new_site.yml` pointing to the new site's secrets.

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| DigiKey 401 | Token expired mid-run | Script auto-refreshes. If persistent, check `clients.json` |
| DigiKey 429 | Rate limit hit | Normal — resumes next run from saved index |
| Newark 403 | Monthly quota exceeded or IP block | Newark is fallback only — DigiKey still works. Contact Element14 support |
| Wix 401 | API key expired | Regenerate at manage.wix.com/account/api-keys |
| Wix 403 | Missing CMS or Stores permissions | Edit API key and enable all Wix Stores + Content Manager permissions |
| `comp_data.csv` dtype errors | Column type mismatch | File loads with `dtype=str` — should not occur |
| Workflow fails at commit | Missing write permission | Settings → Actions → General → set Read and write permissions |
| SKUs not found in store | Wix API pagination bug | Targeted query fallback handles this automatically |

---

## Monitoring

**Check last run:** Go to the **Actions** tab — green checkmark = success, red X = failure.

**Check stock breakdown:** View `full_summary.json` in the repo after each run.

**Check unmatched SKUs:** View `not_in_store.txt` in the repo after each run.

**Check scrape progress:** View `scrape_index.txt` — this is where the next run will start from.

---

## Future Improvements

- [ ] Email report after each run (secrets `EMAIL_USERNAME` and `EMAIL_PASSWORD` are ready — just needs Gmail App Password setup)
- [ ] NI SFTP pricing — automate price updates from NI's SFTP server (FileZilla already set up)
- [ ] Newark weekly schedule — run Newark on a separate less-frequent schedule to stay within API limits
- [ ] Price updates — push DigiKey/Newark prices to Wix Store product price field
- [ ] Obsolete product auto-hide — automatically hide obsolete products from the store

---

*Maintained by Cyth Systems IT — github.com/Cyth-code/cyth-inventory-scraper*
