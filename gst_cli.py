"""Command line entry point.

Examples
    python gst_cli.py 27AAPFU0939F1ZV 29AAACI1681G1ZK
    python gst_cli.py --file gstins.xlsx
    python gst_cli.py --file gstins.xlsx --column "GST No" --fy 2024-2025 2025-2026
    python gst_cli.py                      (then paste GSTINs, finish with an empty line)
"""

import argparse
import datetime as dt
import logging
import os
import sys

from gst_gstin import extract_gstins
from gst_io_utils import read_gstins_from_file, write_excel
from gst_runner import OUT_DIR as DEFAULT_OUT_DIR, RunOptions, run_batch

log = logging.getLogger("gst_search")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="gst_search", description="Fetch public GSTIN details from the GST portal.")
    p.add_argument("gstins", nargs="*", help="GSTINs (any separator). Omit to paste interactively.")
    p.add_argument("-f", "--file", help="Excel / CSV / TXT file containing GSTINs")
    p.add_argument("-c", "--column", help="Column name holding GSTINs (auto-detected if omitted)")
    p.add_argument("--sheet", help="Excel sheet name (default: first sheet)")
    p.add_argument("-o", "--out", help="Output .xlsx path (default: output/gst_details_<timestamp>.xlsx)")
    p.add_argument("--solver", default="auto", choices=["auto", "ddddocr", "tesseract", "manual"])
    p.add_argument("--manual-after", type=int, default=None, metavar="N",
                   help="Ask you to type the captcha after N failed automatic attempts")
    p.add_argument("--tesseract-cmd", help="Path to tesseract.exe if not on the default location")
    p.add_argument("--max-attempts", type=int, default=8, help="Captcha attempts per GSTIN (default 8)")
    p.add_argument("--delay", type=float, default=0.0,
                   help="Pause per worker between GSTINs in seconds (default 0)")
    p.add_argument("-w", "--workers", type=int, default=8,
                   help="GSTINs searched in parallel (default 8; lower it if the portal starts blocking)")
    p.add_argument("--no-goods", action="store_true", help="Skip HSN/SAC goods & services lookup")
    p.add_argument("--no-returns", action="store_true", help="Skip return-filing history")
    p.add_argument("--fy", nargs="+", help="Financial years for return filing, e.g. 2024-2025 2025-2026 "
                                           "(default: what the portal shows, ~last 2 years)")
    p.add_argument("--summary-only", action="store_true",
                   help="Write only the one-row-per-GSTIN summary sheet (no detail sheets)")
    p.add_argument("--open", action="store_true", help="Open the Excel file when finished")
    p.add_argument("--max-age", type=float, default=7, metavar="DAYS",
                   help="Reuse a GSTIN's saved result if fetched within this many days (default 7, 0 = never)")
    p.add_argument("--refresh", action="store_true", help="Ignore saved results and fetch everything fresh")
    p.add_argument("--resume", action="store_true", help="Reuse any saved result regardless of age")
    p.add_argument("--skip-checksum", action="store_true", help="Search GSTINs even if their checksum fails")
    p.add_argument("--debug", action="store_true", help="Verbose log + save captchas/responses to output/debug")
    return p.parse_args(argv)


def collect_gstins(args):
    gstins = extract_gstins(" ".join(args.gstins)) if args.gstins else []
    if args.file:
        gstins += read_gstins_from_file(args.file, args.column, args.sheet)
    if not args.gstins and not args.file:
        print("Paste or type GST numbers (one per line or separated by spaces/commas).")
        print("When done, press Enter on an empty line to start:\n")
        lines = []
        for line in sys.stdin:
            if not line.strip():
                break
            lines.append(line)
        gstins = extract_gstins(" ".join(lines))
    return list(dict.fromkeys(gstins))


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)

    gstins = collect_gstins(args)
    if not gstins:
        log.error("No GSTINs found.")
        return 1

    out_dir = os.path.dirname(os.path.abspath(args.out)) if args.out else DEFAULT_OUT_DIR
    out_path = args.out or os.path.join(out_dir, f"gst_details_{dt.datetime.now():%Y%m%d_%H%M%S}.xlsx")

    opts = RunOptions(
        workers=args.workers, delay=args.delay, max_attempts=args.max_attempts,
        goods=not args.no_goods, returns=not args.no_returns, fys=args.fy,
        max_age=args.max_age, refresh=args.refresh, resume=args.resume,
        skip_checksum=args.skip_checksum, debug=args.debug, solver_mode=args.solver,
        manual_after=args.manual_after, tesseract_cmd=args.tesseract_cmd)
    run = run_batch(gstins, opts)
    results, stats = run["records"], run["stats"]

    if results:
        counts = write_excel(results, out_path, summary_only=args.summary_only)
        log.info("Done in %.0fs: %d OK, %d failed. Captchas submitted: %d (wrong: %d)",
                 stats["duration_s"], stats["ok"], stats["failed"], stats["captcha_submitted"],
                 stats["captcha_wrong"])
        if stats["timings"]:
            log.info("Average time per step: %s", ", ".join(
                f"{stage} {secs:.2f}s" for stage, secs in sorted(stats["timings"].items())))
        log.info("Sheets: %s", ", ".join(f"{k}={v}" for k, v in counts.items() if v))
        log.info("Saved: %s", out_path)
        if args.open and os.name == "nt":
            os.startfile(os.path.abspath(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
