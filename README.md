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
* Captcha samples and run history live on the app's server disk. They are lost whenever the app
  restarts or redeploys, so download the Excel file you need.
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
python gst_cli.py 27AAPFU0939F1ZV 29AAACI1681G1ZK

# Paste a list: run with no arguments, paste, then press Enter on an empty line
python gst_cli.py

# From Excel / CSV / TXT (the GSTIN column is auto-detected)
python gst_cli.py --file "E:\GST Automation\vendors.xlsx"
python gst_cli.py --file vendors.xlsx --column "GST No" --sheet Sheet2

# Return-filing history for specific financial years (default: what the portal shows)
python gst_cli.py --file vendors.xlsx --fy 2024-2025 2025-2026
```

Or double-click `run.bat` (paste mode) / drag an Excel file onto it.

## Output

`output/gst_details_<timestamp>.xlsx` with one sheet, **GST Summary**. It has one row per input
entry, in the same order as the input (repeated GSTINs repeat; invalid entries appear as error
rows). The columns are: GSTIN, Legal Name, Trade Name, Date of Registration, Administrative Office,
GSTIN Status, Nature Of Core Business Activity, Constitution of Business, Other Office, Taxpayer
Type, PPOB, Aadhaar Authenticated, HSN, Last GSTR-1 Filed, Last GSTR-3B Filed. Every GSTIN is
fetched fresh from the portal on every run.

`--all-sheets` also adds detail sheets: Taxpayer Details, Additional Places, Goods & Services
(with descriptions), Return Filing (full history) and Errors. The raw portal data for each GSTIN
is kept in `output/raw/<GSTIN>.json` for troubleshooting.

## Useful options

| Option | Meaning |
|---|---|
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
python gst_captcha_lab.py collect --n 100   # live accuracy + confidence report
python gst_captcha_lab.py bench             # compare OCR strategies offline
```

## Speed

Three things run in parallel:
* 8 captcha workers, each with its own portal session. Each one prepares its next captcha in
  the background.
* A separate pool of download threads for goods/services and return filing. These need no
  captcha, so the captcha workers never wait for them.
* The background captcha preparation inside each worker.

Measured on 104 GSTINs: 24 s with 8 workers. With 12 or 16 workers the portal started forcibly
closing connections and the same run took 104–271 s, so don't go above 8. Each run ends with an
"Average time per step" line showing where the time went. Failed GSTINs are retried once, one at
a time, at the end.

## Troubleshooting

* **Captchas keep failing** – run with `--debug` and check the images in `output/debug`
  (file names show the OCR guess). Add `--manual-after 3` as a safety net.
* **"Unexpected non-JSON response" / HTTP 403** – the portal is throttling you; increase `--delay`.
* **Portal changed its API** – endpoint URLs are in `gst_client.py` (`ENDPOINTS`);
  compare against your browser's DevTools → Network tab while doing one manual search.

Use responsibly: this reads only publicly available information. Keep request rates modest,
and for high volume business use consider an official GST Suvidha Provider (GSP) API.
