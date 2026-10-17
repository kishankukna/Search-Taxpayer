"""HTTP client for the GST portal's public 'Search Taxpayer' service."""

import json
import logging
import os
import random
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger(__name__)

BASE = "https://services.gst.gov.in/services"
SEARCH_PAGE = f"{BASE}/searchtp"

# Endpoints used by the portal's search page. If the portal changes, adjust them here.
ENDPOINTS = {
    "captcha": f"{BASE}/captcha",
    "details": f"{BASE}/api/search/taxpayerDetails",          # POST {"gstin", "captcha"}
    "goods": f"{BASE}/api/search/goodservice",                # GET ?gstin=
    "returns": f"{BASE}/api/search/taxpayerReturnDetails",    # POST {"gstin"[, "fy"]}
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://services.gst.gov.in",
    "Referer": SEARCH_PAGE,
}

# Error codes / messages the portal returns for a wrong captcha.
CAPTCHA_ERROR_CODES = {"SWEB_9000", "SWEB_9001", "SWEB_9035"}
CAPTCHA_ERROR_RE = re.compile(r"captcha|letters|characters shown|image", re.I)
# Transient "something went wrong" errors worth retrying.
TRANSIENT_CODES = {"SWEB_8000"}

_samples_lock = threading.Lock()


class GSTPortalError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _extract_error(data):
    """Return (code, message) if the JSON payload is an error response, else None."""
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        return err.get("error_cd") or err.get("errorCode"), err.get("message") or err.get("msg") or str(err)
    if data.get("errorCode") or data.get("errorcode"):
        code = data.get("errorCode") or data.get("errorcode")
        return code, data.get("message") or data.get("errorMessage") or data.get("msg") or code
    if isinstance(err, str) and err:
        return None, err
    return None


class GSTPortalClient:
    """One portal session. Use one client per worker thread (the solver can be shared).

    Speed tricks: goods/services and return filing don't need the captcha, so they are fetched
    in the background while the captcha search runs; and after a successful search the next
    captcha is fetched and read in the background, ready for the next GSTIN."""

    def __init__(self, solver, timeout=30, delay=0.0, max_captcha_attempts=8, max_fetches=8,
                 debug_dir=None, samples_dir=None, prefetch=True):
        self.solver = solver
        self.timeout = timeout
        self.delay = delay
        self.max_captcha_attempts = max_captcha_attempts
        self.max_fetches = max_fetches  # captcha images to try per submission while waiting for a confident read
        self.debug_dir = debug_dir
        self.samples_dir = samples_dir
        self.prefetch = prefetch
        self.session = None
        self.stats = defaultdict(int)
        self.timings = defaultdict(list)  # stage -> seconds, for the end-of-run report
        self._stats_lock = threading.Lock()
        self._bg = ThreadPoolExecutor(max_workers=3)
        self._next_captcha = None  # (session generation, future of (image, answer))
        self._generation = 0
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
        self._new_session()

    # ---------------------------------------------------------------- plumbing
    def _new_session(self):
        if self.session:
            self.session.close()
        self._generation += 1
        self._next_captcha = None  # a prefetched captcha belongs to the old session
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8)
        self.session.mount("https://", adapter)
        try:
            self.session.get(SEARCH_PAGE, timeout=self.timeout)  # picks up portal cookies
        except requests.RequestException as exc:
            log.debug("Warm-up request failed: %s", exc)

    def _count(self, key, n=1):
        with self._stats_lock:
            self.stats[key] += n

    def _time(self, stage, seconds):
        with self._stats_lock:
            self.timings[stage].append(seconds)

    def close(self):
        self._bg.shutdown(wait=False, cancel_futures=True)
        if self.session:
            self.session.close()

    def pause(self, factor=1.0):
        if self.delay:
            time.sleep(self.delay * factor * random.uniform(0.7, 1.3))

    def _request(self, method, url, retries=3, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                if attempt == retries:
                    raise GSTPortalError(f"Network error: {exc}") from exc
                log.warning("Network error (%s), retrying...", exc)
                time.sleep(3 * attempt)
                continue
            if resp.status_code in (403, 429, 502, 503, 504):
                if attempt == retries:
                    raise GSTPortalError(f"Portal returned HTTP {resp.status_code}", code=resp.status_code)
                wait = 20 * attempt
                log.warning("HTTP %s from portal - backing off %ss", resp.status_code, wait)
                time.sleep(wait)
                self._new_session()
                continue
            return resp
        raise GSTPortalError("Request failed")

    def _json(self, resp, tag):
        try:
            data = resp.json()
        except ValueError:
            self._dump(f"{tag}_nonjson.html", resp.text)
            raise GSTPortalError(f"Unexpected non-JSON response (HTTP {resp.status_code})")
        self._dump(f"{tag}.json", json.dumps(data, indent=2, ensure_ascii=False))
        return data

    def _dump(self, name, content, binary=False):
        if not self.debug_dir:
            return
        path = os.path.join(self.debug_dir, f"{int(time.time() * 1000)}_{name}")
        with open(path, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
            f.write(content)

    def save_sample(self, image, label, accepted):
        """Keep captchas with their outcome: accepted ones are verified labels for tuning the OCR."""
        if not self.samples_dir:
            return
        folder = os.path.join(self.samples_dir, "ok" if accepted else "rejected")
        with _samples_lock:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{label}_{int(time.time() * 1000)}_{random.randint(0, 999):03d}.png")
            with open(path, "wb") as f:
                f.write(image)

    # ---------------------------------------------------------------- captcha
    def fetch_captcha(self):
        resp = self._request("GET", ENDPOINTS["captcha"], params={"rnd": random.random()},
                             headers={"Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8"})
        if not resp.content or "image" not in resp.headers.get("Content-Type", "image"):
            raise GSTPortalError("Could not load captcha image")
        self._count("captcha_fetched")
        return resp.content

    def read_new_captcha(self, attempt=1):
        """Fetch captcha images until the solver is confident; returns (image, answer).
        Fetching a new image is much cheaper than a wrong submission, so unsure reads are skipped,
        except on the last fetch where the best 6-digit reading is used anyway."""
        t0 = time.perf_counter()
        try:
            for i in range(1, self.max_fetches + 1):
                t1 = time.perf_counter()
                image = self.fetch_captcha()
                t2 = time.perf_counter()
                answer, confident = self.solver.read(image, attempt=attempt, last_chance=(i == self.max_fetches))
                self._time("captcha download", t2 - t1)
                self._time("captcha OCR", time.perf_counter() - t2)
                self._dump(f"captcha_{answer or 'unsolved'}_{'sure' if confident else 'unsure'}.png", image,
                           binary=True)
                if answer and (confident or i == self.max_fetches):
                    return image, answer
            raise GSTPortalError(f"Captcha solver could not read {self.max_fetches} consecutive captcha images")
        finally:
            self._time("captcha fetch+read", time.perf_counter() - t0)

    def _start_prefetch(self):
        if self.prefetch:
            self._next_captcha = (self._generation, self._bg.submit(self.read_new_captcha))

    def _take_prefetched(self):
        """A captcha read in the background after the previous GSTIN, if still valid."""
        pending, self._next_captcha = self._next_captcha, None
        if not pending or pending[0] != self._generation:
            return None
        t0 = time.perf_counter()
        try:
            return pending[1].result()
        except Exception as exc:
            log.debug("Prefetched captcha unusable: %s", exc)
            return None
        finally:
            self._time("waiting for prefetched captcha", time.perf_counter() - t0)

    def submit_search(self, gstin, captcha):
        """One search attempt. Returns the details dict, or None if the portal rejected the captcha."""
        self._count("captcha_submitted")
        t0 = time.perf_counter()
        resp = self._request("POST", ENDPOINTS["details"], json={"gstin": gstin, "captcha": captcha},
                             headers={"Content-Type": "application/json;charset=UTF-8"})
        self._time("search request", time.perf_counter() - t0)
        data = self._json(resp, f"details_{gstin}")
        err = _extract_error(data)
        if err is None and isinstance(data, dict) and (data.get("gstin") or data.get("lgnm")):
            return data
        code, message = err or (None, f"Unrecognised response: {str(data)[:200]}")
        if code in CAPTCHA_ERROR_CODES or CAPTCHA_ERROR_RE.search(str(message)):
            self._count("captcha_wrong")
            return None
        raise GSTPortalError(message, code)  # e.g. invalid / non-existent GSTIN

    # ---------------------------------------------------------------- API
    def search(self, gstin):
        """Taxpayer details for one GSTIN (the main search result)."""
        for attempt in range(1, self.max_captcha_attempts + 1):
            ready = self._take_prefetched() if attempt == 1 else None
            image, captcha = ready or self.read_new_captcha(attempt)
            data = self.submit_search(gstin, captcha)
            self.save_sample(image, captcha, accepted=data is not None)
            if data is not None:
                self._start_prefetch()  # next GSTIN's captcha gets ready while we finish this one
                return data
            log.info("  %s: captcha '%s' rejected (attempt %d/%d)", gstin, captcha, attempt,
                     self.max_captcha_attempts)
            if attempt % 3 == 0:
                self._new_session()
        raise GSTPortalError("Captcha attempts exhausted")

    def _get_json(self, method, endpoint, tag, retries=3, **kwargs):
        for attempt in range(1, retries + 1):
            t0 = time.perf_counter()
            resp = self._request(method, endpoint, **kwargs)
            self._time(tag.split("_")[0] + " request", time.perf_counter() - t0)
            data = self._json(resp, tag)
            err = _extract_error(data)
            if not err:
                return data
            if err[0] not in TRANSIENT_CODES or attempt == retries:
                raise GSTPortalError(err[1], err[0])
            time.sleep(1.5 * attempt)

    def goods_services(self, gstin):
        """HSN/SAC codes of goods & services dealt in. Uses the session of the last successful search."""
        return self._get_json("GET", ENDPOINTS["goods"], f"goods_{gstin}", params={"gstin": gstin})

    def return_filing(self, gstin, fy=None):
        """Return-filing history (GSTR-1 / GSTR-3B etc). `fy` like '2024-2025'; None = portal default."""
        payload = {"gstin": gstin}
        if fy:
            payload["fy"] = fy
        return self._get_json("POST", ENDPOINTS["returns"], f"returns_{gstin}_{fy or 'default'}", json=payload,
                              headers={"Content-Type": "application/json;charset=UTF-8"})

    def fetch_all(self, gstin, goods=True, returns=False, fys=None):
        """Everything available for one GSTIN. Goods/returns run in the background alongside the
        captcha search (they don't need it) and fail independently of it."""
        t0 = time.perf_counter()
        goods_job = self._bg.submit(self.goods_services, gstin) if goods else None
        return_jobs = {fy or "default": self._bg.submit(self.return_filing, gstin, fy)
                       for fy in (fys or [None])} if returns else {}
        try:
            details = self.search(gstin)
        except Exception:
            for job in [goods_job, *return_jobs.values()]:
                if job:
                    job.cancel()
            raise
        record = {"gstin": gstin, "details": details, "goods": None, "returns": None, "warnings": []}
        if goods_job:
            try:
                record["goods"] = goods_job.result()
            except GSTPortalError as exc:
                record["warnings"].append(f"goods/services: {exc}")
        if returns:
            record["returns"] = {}
            for fy, job in return_jobs.items():
                try:
                    record["returns"][fy] = job.result()
                except GSTPortalError as exc:
                    record["warnings"].append(f"returns {'' if fy == 'default' else fy}: {exc}".strip())
        self._time("total per GSTIN", time.perf_counter() - t0)
        return record
