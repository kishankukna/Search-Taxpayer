# GST Taxpayer Search Automation

## Web dashboard (Streamlit)

`app.py` is the dashboard. Paste GST numbers or upload an Excel/CSV/TXT file, press
**Fetch details**, then see KPIs (run performance, status, GSTR-3B compliance, captcha accuracy,
run history), filter the results table and download the Excel file.

Run locally: `streamlit run app.py`

### Deploy on Streamlit Community Cloud
1. Put the code on GitHub. To upload through the GitHub web page (which drops folders), run
   `python make_github_upload.py`. It builds `..\github_upload`, a flat copy where every file is
   at the top level (package modules become `gst_*.py`). Upload all files in that folder. With
   `git push` instead, push this folder as it is. Either way, `app.py`, `requirements.txt` and
   `packages.txt` must be at the top level of the repo.
2. Go to https://share.streamlit.io, choose **Create app → Deploy a public app from GitHub**,
   pick the repo and branch, and set **Main file path** to `app.py`.
3. Under **Advanced settings**, choose **Python 3.12**. Under **Secrets**, you can add
   `APP_PASSWORD = "your-password"` to lock the app; without it, anyone with the link can use it.
4. Press **Deploy**. Every `git push` to that branch redeploys the app automatically.

Notes for the hosted app:
* Saved results, captcha samples and run history live on the app's server disk. They are lost
  whenever the app restarts or redeploys, so download the Excel file you need.
* The free tier has limited CPU. 4 workers is the default; 8 may not be faster there.
* The app calls the GST portal from Streamlit's servers (outside India). If the portal refuses
  those connections, every GSTIN fails with a network/HTTP error; in that case run it locally
  (`streamlit run app.py`) or host it on an Indian server.

Fetches the public details shown on https://services.gst.gov.in/services/searchtp for a list
of GSTINs and solves the 6-digit captcha automatically with offline OCR.

## Setup (one time)

1. Install Python 3.10–3.12 (already done on this PC: `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`).
2. In this `automation` folder:
   ```
   python -m pip install -r requirements.txt
   ```
   If `python` opens the Microsoft Store, open a new terminal or turn off the
   "App execution aliases" for python in Windows Settings.

## Usage (run from the `automation` folder)

```
# GSTINs on the command line (any separator)
python -m gst_search 27AAPFU0939F1ZV 29AAACI1681G1ZK

# Paste a list: run with no arguments, paste, then press Enter on an empty line
python -m gst_search

# From Excel / CSV / TXT (the GSTIN column is auto-detected)
python -m gst_search --file "E:\GST Automation\vendors.xlsx"
python -m gst_search --file vendors.xlsx --column "GST No" --sheet Sheet2

# Return-filing history for specific financial years (default: what the portal shows)
python -m gst_search --file vendors.xlsx --fy 2024-2025 2025-2026
```

Or double-click `run.bat` (paste mode) / drag an Excel file onto it.

## Output

`output/gst_details_<timestamp>.xlsx` with sheets:

| Sheet | Contents |
|---|---|
| GST Summary | One row per GSTIN: GSTIN, Legal Name, Trade Name, Date of Registration, Status, Nature of Business, Constitution, Administrative Office, Other Office, Taxpayer Type, PPoB, Aadhaar Authenticated, HSN, Last GSTR-1 Filed, Last GSTR-3B Filed (use `--summary-only` to get just this sheet) |
| Taxpayer Details | Legal/trade name, status, registration & cancellation dates, taxpayer type, constitution, nature of business, state/centre jurisdiction, principal address, Aadhaar/e-KYC flags, e-invoice status, partners/directors, last updated — plus any other field the portal returns |
| Additional Places | Every additional place of business |
| Goods & Services | HSN/SAC codes and descriptions |
| Return Filing | Return type, period, filing date, ARN, status (skip with `--no-returns`) |
| Errors | GSTINs that failed and why |

The full raw JSON for each GSTIN is also saved in `output/raw/<GSTIN>.json`.

## Useful options

| Option | Meaning |
|---|---|
| `--max-age 7` | Reuse a GSTIN's saved result if it was fetched within this many days (default 7; `0` = always fetch) |
| `--refresh` | Ignore saved results and fetch everything fresh (e.g. to catch a status change today) |
| `--resume` | Reuse any saved result, however old (use after an interruption) |
| `--workers 8` | GSTINs searched in parallel (default 8). Lower to 2–4 if the portal starts returning HTTP 403/429 or slows down |
| `--delay 0` | Pause per worker between GSTINs (default 0) |
| `--max-attempts 8` | Captcha attempts per GSTIN |
| `--manual-after 5` | After 5 wrong automatic captchas, show the image and let you type it |
| `--solver manual` | Always type captchas yourself |
| `--no-goods` | Skip the HSN/SAC lookup |
| `--debug` | Save every captcha image and portal response in `output/debug` |

## How the captcha is solved

Two offline neural OCR models (from `ddddocr`) read three cleaned versions of each image
(grid and red line removed). Their output is decoded so it can only be exactly 6 digits.
A captcha is submitted only when both models agree with enough confidence; otherwise a fresh
image is fetched, which takes about 0.2 s. A wrong captcha is retried automatically, so it never
loses a GSTIN.

Every captcha the portal accepts is saved in `output/captcha_samples/ok` as a verified example.
To measure or re-tune accuracy:
```
python -m gst_search.captcha_lab collect --n 100   # live accuracy + confidence report
python -m gst_search.captcha_lab bench             # compare OCR strategies offline
```

## Speed

The tool runs 8 workers in parallel. Goods/services and return filing are fetched while the
captcha is being solved, and the next captcha is prepared in the background. Each run ends
with an "Average time per step" line showing where the time went. Failed GSTINs are retried
once, one at a time, at the end. GSTINs fetched in the last 7 days are taken from
`output/raw` instantly.

## Troubleshooting

* **Captchas keep failing** – run with `--debug` and check the images in `output/debug`
  (file names show the OCR guess). Add `--manual-after 3` as a safety net.
* **"Unexpected non-JSON response" / HTTP 403** – the portal is throttling you; increase `--delay`.
* **Portal changed its API** – endpoint URLs are in `gst_search/client.py` (`ENDPOINTS`);
  compare against your browser's DevTools → Network tab while doing one manual search.

Use responsibly: this reads only publicly available information. Keep request rates modest,
and for high volume business use consider an official GST Suvidha Provider (GSP) API.
