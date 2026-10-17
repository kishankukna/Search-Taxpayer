"""Batch engine shared by the command line (cli.py) and the Streamlit dashboard (app.py)."""

import datetime as dt
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

from gst_captcha import build_solver
from gst_client import GSTPortalClient, GSTPortalError
from gst_gstin import is_valid_gstin
from gst_io_utils import load_raw, save_raw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
RAW_DIR = os.path.join(OUT_DIR, "raw")
SAMPLES_DIR = os.path.join(OUT_DIR, "captcha_samples")
HISTORY_PATH = os.path.join(OUT_DIR, "run_history.jsonl")

log = logging.getLogger("gst_search")


@dataclass
class RunOptions:
    workers: int = 8
    delay: float = 0.0
    max_attempts: int = 8
    goods: bool = True
    returns: bool = True
    fys: list = None
    max_age: float = 7          # days a saved result stays reusable
    refresh: bool = False       # ignore saved results
    resume: bool = False        # reuse saved results of any age
    skip_checksum: bool = False
    debug: bool = False
    solver_mode: str = "auto"
    manual_after: int = None
    tesseract_cmd: str = None


def age_days(record):
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(record["fetched_at"])).total_seconds() / 86400
    except (KeyError, TypeError, ValueError):
        return float("inf")


def run_batch(gstins, opts=None, solver=None, progress=None):
    """Fetch every GSTIN. `progress(done, total, record, source)` is called from the calling
    thread after each GSTIN (source: 'invalid' | 'cache' | 'portal' | 'retry').
    Returns {"records": [...in input order...], "stats": {...}}."""
    opts = opts or RunOptions()
    gstins = list(dict.fromkeys(gstins))
    total = len(gstins)
    started_at = dt.datetime.now()
    t0 = time.time()
    results, todo = {}, []
    counts = {"invalid": 0, "reused": 0}
    done = 0

    def report(record, source):
        nonlocal done
        done += 1
        if progress:
            progress(done, total, record, source)

    for i, gstin in enumerate(gstins, 1):
        now = dt.datetime.now().isoformat(timespec="seconds")
        if not opts.skip_checksum and not is_valid_gstin(gstin):
            log.warning("[%d/%d] %s - invalid GSTIN checksum, skipped", i, total, gstin)
            results[gstin] = {"gstin": gstin, "ok": False, "error": "Invalid GSTIN (checksum failed)",
                              "fetched_at": now}
            counts["invalid"] += 1
            report(results[gstin], "invalid")
            continue
        cached = None if opts.refresh else load_raw(RAW_DIR, gstin)
        if cached and cached.get("ok") and (opts.resume or age_days(cached) < opts.max_age):
            results[gstin] = cached
            counts["reused"] += 1
            report(cached, "cache")
            continue
        todo.append((i, gstin))
    if counts["reused"]:
        log.info("%d GSTIN(s) reused from results saved within the last %g day(s) (use --refresh to re-fetch)",
                 counts["reused"], opts.max_age)

    workers = 1 if opts.solver_mode == "manual" or opts.manual_after else max(1, opts.workers)
    local = threading.local()
    clients = []

    def get_client():
        if not hasattr(local, "client"):
            local.client = GSTPortalClient(
                solver, delay=opts.delay, max_captcha_attempts=opts.max_attempts,
                debug_dir=os.path.join(OUT_DIR, "debug") if opts.debug else None,
                samples_dir=SAMPLES_DIR)
            clients.append(local.client)
        return local.client

    def work(i, gstin):
        client = get_client()
        now = dt.datetime.now().isoformat(timespec="seconds")
        try:
            record = client.fetch_all(gstin, goods=opts.goods, returns=opts.returns, fys=opts.fys)
            record.update(ok=True, fetched_at=now)
            d = record["details"]
            log.info("[%d/%d] %s  OK  %s | %s | %s", i, total, gstin, d.get("lgnm"), d.get("tradeNam"), d.get("sts"))
            for w in record["warnings"]:
                log.warning("    %s: %s", gstin, w)
        except GSTPortalError as exc:
            record = {"gstin": gstin, "ok": False, "error": str(exc), "fetched_at": now}
            log.error("[%d/%d] %s  FAILED: %s", i, total, gstin, exc)
        save_raw(RAW_DIR, record)
        client.pause()
        return record

    retried = 0
    if todo:
        if solver is None:
            solver = build_solver(opts.solver_mode, opts.manual_after, opts.tesseract_cmd)
        log.info("%d GSTIN(s) to search with %d parallel worker(s)", len(todo), min(workers, len(todo)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(work, i, g) for i, g in todo]
            for fut in as_completed(futures):
                rec = fut.result()
                results[rec["gstin"]] = rec
                if rec.get("ok"):
                    report(rec, "portal")
            pool.shutdown()
            # Second pass: retry failures one at a time (usually temporary portal slowness / throttling).
            failed = [(i, g) for i, g in todo if not results[g].get("ok")]
            if failed:
                log.info("Retrying %d failed GSTIN(s) one at a time...", len(failed))
                time.sleep(5)
                for i, g in failed:
                    results[g] = work(i, g)
                    retried += 1
                    report(results[g], "retry")
        except KeyboardInterrupt:
            log.warning("Interrupted - saving what was fetched so far (re-run with --resume to continue)")
            pool.shutdown(wait=False, cancel_futures=True)

    records = [results[g] for g in gstins if g in results]  # input order
    timings = {}
    for c in clients:
        for stage, values in c.timings.items():
            timings.setdefault(stage, []).extend(values)
    stats = {
        "started_at": started_at.isoformat(timespec="seconds"),
        "duration_s": round(time.time() - t0, 1),
        "total": total,
        "ok": sum(1 for r in records if r.get("ok")),
        "failed": sum(1 for r in records if not r.get("ok")),
        "invalid": counts["invalid"],
        "reused": counts["reused"],
        "searched": len(todo),
        "retried": retried,
        "workers": min(workers, len(todo)) if todo else 0,
        "captcha_fetched": sum(c.stats["captcha_fetched"] for c in clients),
        "captcha_submitted": sum(c.stats["captcha_submitted"] for c in clients),
        "captcha_wrong": sum(c.stats["captcha_wrong"] for c in clients),
        "timings": {k: round(sum(v) / len(v), 3) for k, v in timings.items()},
        "options": {k: v for k, v in asdict(opts).items() if k in ("workers", "max_age", "refresh", "goods",
                                                                   "returns")},
    }
    for c in clients:
        c.close()
    _append_history(stats)
    return {"records": records, "stats": stats}


def _append_history(stats):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(stats) + "\n")
    except OSError as exc:
        log.debug("Could not write run history: %s", exc)


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
