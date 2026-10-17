"""Batch engine shared by the command line (cli.py) and the Streamlit dashboard (app.py)."""

import datetime as dt
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

from gst_captcha import build_solver
from gst_client import GSTPortalClient, GSTPortalError
from gst_gstin import is_valid_gstin
from gst_io_utils import save_raw

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
    skip_checksum: bool = False
    debug: bool = False
    solver_mode: str = "auto"
    manual_after: int = None
    tesseract_cmd: str = None


def run_batch(entries, opts=None, solver=None, progress=None):
    """Fetch every GSTIN fresh from the portal. `entries` is the input in its original order,
    duplicates allowed; each unique GSTIN is fetched once, but `records` has exactly one record per
    entry, in the same order, so output row N always matches input row N (parallel workers finish
    out of order). `progress(done, total, record, source)` is called from the calling thread after
    each unique GSTIN (source: 'invalid' | 'portal' | 'retry').
    Returns {"records": [...], "stats": {...}}."""
    opts = opts or RunOptions()
    entries = list(entries)
    gstins = list(dict.fromkeys(entries))
    total = len(gstins)
    started_at = dt.datetime.now()
    t0 = time.time()
    results, todo = {}, []
    counts = {"invalid": 0}
    done = 0

    def report(record, source):
        nonlocal done
        done += 1
        if progress:
            progress(done, total, record, source)

    for i, gstin in enumerate(gstins, 1):
        now = dt.datetime.now().isoformat(timespec="seconds")
        if not opts.skip_checksum and not is_valid_gstin(gstin):
            reason = ("Invalid GSTIN (checksum failed)" if re.fullmatch(r"\d{2}[0-9A-Z]{13}", gstin)
                      else "Not a valid GSTIN format")
            log.warning("[%d/%d] %s - %s, skipped", i, total, gstin, reason)
            results[gstin] = {"gstin": gstin, "ok": False, "error": reason, "fetched_at": now}
            counts["invalid"] += 1
            report(results[gstin], "invalid")
            continue
        todo.append((i, gstin))

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

    # Goods/services and return filing need no captcha, so they run in their own pool of download
    # threads, started for every GSTIN up front. The captcha workers only do the searches and never
    # wait for these downloads, which matters when return filing is slow (2+ s on a busy portal).
    dl_local = threading.local()
    dl_clients = []

    def get_dl_client():
        if not hasattr(dl_local, "client"):
            dl_local.client = GSTPortalClient(None, prefetch=False)
            dl_clients.append(dl_local.client)
        return dl_local.client

    def download_goods(gstin):
        return get_dl_client().goods_services(gstin)

    def download_returns(gstin, fy):
        return get_dl_client().return_filing(gstin, fy)

    side_jobs = {}

    def start_downloads(pool, gstin):
        jobs = {}
        if opts.goods:
            jobs["goods"] = pool.submit(download_goods, gstin)
        if opts.returns:
            for fy in (opts.fys or [None]):
                jobs[("returns", fy or "default")] = pool.submit(download_returns, gstin, fy)
        side_jobs[gstin] = jobs

    def search(i, gstin):
        """Captcha search only. Returns (details, None) or (None, error message)."""
        client = get_client()
        try:
            return client.search(gstin), None
        except GSTPortalError as exc:
            return None, str(exc)
        finally:
            client.pause()

    def assemble(i, gstin, details, error):
        now = dt.datetime.now().isoformat(timespec="seconds")
        if details is None:
            record = {"gstin": gstin, "ok": False, "error": error, "fetched_at": now}
            log.error("[%d/%d] %s  FAILED: %s", i, total, gstin, error)
        else:
            record = {"gstin": gstin, "details": details, "goods": None, "returns": {} if opts.returns else None,
                      "warnings": [], "ok": True, "fetched_at": now}
            for key, job in side_jobs.get(gstin, {}).items():
                try:
                    value = job.result()
                except GSTPortalError as exc:
                    label = "goods/services" if key == "goods" else f"returns {'' if key[1] == 'default' else key[1]}"
                    record["warnings"].append(f"{label.strip()}: {exc}")
                    continue
                if key == "goods":
                    record["goods"] = value
                else:
                    record["returns"][key[1]] = value
            log.info("[%d/%d] %s  OK  %s | %s | %s", i, total, gstin, details.get("lgnm"), details.get("tradeNam"),
                     details.get("sts"))
            for w in record["warnings"]:
                log.warning("    %s: %s", gstin, w)
        save_raw(RAW_DIR, record)
        return record

    retried = 0
    if todo:
        if solver is None:
            solver = build_solver(opts.solver_mode, opts.manual_after, opts.tesseract_cmd)
        log.info("%d GSTIN(s) to search with %d parallel worker(s)", len(todo), min(workers, len(todo)))
        dl_pool = ThreadPoolExecutor(max_workers=max(4, workers), thread_name_prefix="download")
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="captcha")
        try:
            for _, g in todo:
                start_downloads(dl_pool, g)
            futures = {pool.submit(search, i, g): (i, g) for i, g in todo}
            for fut in as_completed(futures):
                i, g = futures[fut]
                rec = assemble(i, g, *fut.result())
                results[g] = rec
                if rec.get("ok"):
                    report(rec, "portal")
            pool.shutdown()
            # Second pass: retry failures one at a time (usually temporary portal slowness / throttling).
            failed = [(i, g) for i, g in todo if not results[g].get("ok")]
            if failed:
                log.info("Retrying %d failed GSTIN(s) one at a time...", len(failed))
                time.sleep(5)
                for i, g in failed:
                    if any(job.exception() for job in side_jobs.get(g, {}).values()):
                        start_downloads(dl_pool, g)  # the downloads failed too: try them again
                    results[g] = assemble(i, g, *search(i, g))
                    retried += 1
                    report(results[g], "retry")
            dl_pool.shutdown()
        except KeyboardInterrupt:
            log.warning("Interrupted - saving what was fetched so far")
            pool.shutdown(wait=False, cancel_futures=True)
            dl_pool.shutdown(wait=False, cancel_futures=True)

    records = [results[e] for e in entries if e in results]  # one per input entry, input order
    timings = {}
    for c in clients + dl_clients:
        for stage, values in c.timings.items():
            timings.setdefault(stage, []).extend(values)
    stats = {
        "started_at": started_at.isoformat(timespec="seconds"),
        "duration_s": round(time.time() - t0, 1),
        "entries": len(entries),
        "duplicates": len(entries) - total,
        "total": total,
        "ok": sum(1 for g in gstins if results.get(g, {}).get("ok")),
        "failed": sum(1 for g in gstins if not results.get(g, {}).get("ok")),
        "invalid": counts["invalid"],
        "searched": len(todo),
        "retried": retried,
        "workers": min(workers, len(todo)) if todo else 0,
        "captcha_fetched": sum(c.stats["captcha_fetched"] for c in clients),
        "captcha_submitted": sum(c.stats["captcha_submitted"] for c in clients),
        "captcha_wrong": sum(c.stats["captcha_wrong"] for c in clients),
        "timings": {k: round(sum(v) / len(v), 3) for k, v in timings.items()},
        "options": {k: v for k, v in asdict(opts).items() if k in ("workers", "goods",
                                                                   "returns")},
    }
    for c in clients + dl_clients:
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
