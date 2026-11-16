# Donegal Farm – weaner calf price feed

A small public feed of **weekly weaner calf benchmark prices** for the Donegal Farm Herd Tracker
(<https://spectretactical.github.io/donegal-farm/>). The app reads
**<https://spectretactical.github.io/donegal-prices/prices.json>** and shows the prices next to the farm's own sales.

No farm data is in this repository – the farm records stay in the private Firebase database.

## Sources and credits

| Source | What is used | Link |
|---|---|---|
| **RPO – Red Meat Producers’ Organisation** | Weaner calf R/kg live (bulls), national, weekly – full history from the carcass price table | <https://rpo.co.za/carcass-prices/> |
| **AMT** | Weaners 200-250 kg R/kg live (market prices page) plus weaner classes (light / heavy / top 40%) and regional 200-250 kg prices from the *AMT Weekly Livestock Report* PDF | <https://amtrends.co.za/market-pricesv2/> · <https://amtrends.co.za/livestock-weekly-reports-amtrends/> |
| **RMIS – Red Meat Industry Services** | **Link only.** RMIS’s terms do not allow automated collection, so nothing is fetched from RMIS. | <https://rmis.co.za/prices/> |

All figures belong to their publishers. They are reproduced here **for private family use, with attribution**, and are
not a valuation of any animal – please use the publishers’ own sites for authoritative figures. If a publisher asks for
its figures to be removed, delete its section from `scrape.py` and `prices.json`.

## How it works

* `.github/workflows/update-prices.yml` runs `scrape.py` at 06:00, 12:00 and 18:00 SAST (04:00, 10:00, 16:00 UTC) and on
  demand (*Actions → Update weaner prices → Run workflow*). It commits `prices.json` when it changes.
* The sources publish once a week (usually Wednesday–Friday); the extra runs just pick the new week up quickly.
* Each source is handled separately. If one fails (site down, layout changed), its **last good figures are kept**, its
  `status` becomes `error` (or `partial`) with the reason in `error`, and the other source still updates.
* Sanity limits: anything outside R10–R150/kg is treated as a parse error, never published.
* Python standard library only, plus `pdftotext` (poppler-utils) for the AMT PDF.

Run locally: `python3 scrape.py prices.json` · tests: `python3 -m unittest discover -s tests -v`
(the tests use trimmed copies of the real pages saved on 16 Nov 2026 in `tests/fixtures/`).

## prices.json

```jsonc
{
  "schema": 1, "generated_at": "2026-11-16T16:33:00Z", "note": "…",
  "sources": [
    { "id": "rpo", "name": "…", "url": "…", "attribution": "…", "series": "…",
      "status": "ok|partial|error", "error": null, "checked_at": "…", "last_success": "…",
      "latest": { "date": "2026-11-13", "week_ending": "2026-11-06", "value": 48.94, "previous": 49.33 },
      "history": [ { "date": "2026-11-13", "week_ending": "2026-11-06", "value": 48.94 }, … ] },
    { "id": "amt", …, "latest": { "date": "2026-11-13", "week_ending": null, "value": 46.12, "previous": 47.08, "change_pct": -2.04 },
      "report": { "no": 46, "date": "2026-11-13", "url": "…pdf" },
      "classes": [ { "id": "light", "label": "Light weaners (160-200 kg)", "value": 46.35 }, … ],
      "regions": [ { "id": "FS", "label": "Free State (200-250 kg)", "value": 46.85 }, … ],
      "history": [ { "date": "2026-11-13", "value": 46.12, "classes": {…}, "regions": {…}, "report_no": 46 }, … ] },
    { "id": "rmis", "url": "https://rmis.co.za/prices/", "link_only": true, "status": "link", … }
  ]
}
```

Notes: RPO rows from 2026 give a publication date plus “week ended”; older RPO tables list a single weekly date, which is
stored as `week_ending` with `date: null`. AMT history starts with the four weekly reports linked when the feed was set
up (Oct–Nov 2026) and grows by one point a week.
