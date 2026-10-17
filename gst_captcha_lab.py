"""Measure and tune captcha accuracy.

    python gst_captcha_lab.py collect --n 100     # submit real captchas, log answer/confidence/outcome
    python gst_captcha_lab.py bench               # offline accuracy of OCR strategies on verified samples

Verified samples live in output/captcha_samples/ok/<answer>_*.png (every captcha the portal
accepts during normal runs is saved there too). To add hand-labelled hard cases, put images in
output/captcha_samples/labeled/<answer>_<anything>.png.
"""

import argparse
import glob
import json
import logging
import os
import time

from gst_captcha import DdddOcrSolver
from gst_cli import DEFAULT_OUT_DIR
from gst_client import GSTPortalClient

SAMPLES = os.path.join(DEFAULT_OUT_DIR, "captcha_samples")

STRATEGIES = {
    "default (std+beta, 3 views)": dict(),
    "template: raw+tmpl_red+tmpl": dict(variants=("raw", "tmpl_red", "tmpl")),
    "template + thin-line (5 views)": dict(variants=("raw", "clean_red", "clean", "tmpl_red", "tmpl")),
    "tmpl_red+tmpl only": dict(variants=("tmpl_red", "tmpl")),
    "std/v1 raw": dict(models=(("std", "v1"),), variants=("raw",)),
    "std/v1 3 views": dict(models=(("std", "v1"),)),
    "beta/v1 3 views": dict(models=(("beta", "v1"),)),
    "std/v16 3 views": dict(models=(("std", "v16"),)),
    "beta/v16 3 views": dict(models=(("beta", "v16"),)),
    "std+beta v16, 3 views": dict(models=(("std", "v16"), ("beta", "v16"))),
    "std+beta v1, clean_red only": dict(variants=("clean_red",)),
}


class _AlwaysSubmit:
    """Wraps the solver so collect submits every reading and logs its confidence."""

    def __init__(self, solver):
        self.solver, self.last = solver, None

    def read(self, image, attempt=1, last_chance=False):
        answer, conf, detail = self.solver.analyse(image)
        self.last = {"answer": answer, "conf": round(conf, 4),
                     "models": [(t, round(c, 4)) for t, c, _ in detail]}
        return answer, True


def collect(n, gstin, delay):
    solver = _AlwaysSubmit(DdddOcrSolver())
    client = GSTPortalClient(solver, delay=delay, samples_dir=SAMPLES)
    log_path = os.path.join(SAMPLES, "collect_log.jsonl")
    os.makedirs(SAMPLES, exist_ok=True)
    ok = 0
    for i in range(1, n + 1):
        image = client.fetch_captcha()
        solver.read(image)
        entry = dict(solver.last)
        if entry["answer"]:
            accepted = client.submit_search(gstin, entry["answer"]) is not None
            client.save_sample(image, entry["answer"], accepted)
        else:
            accepted = False
        ok += accepted
        entry["accepted"] = accepted
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"{i:3d} {entry['answer']} conf={entry['conf']:.2f} {'OK ' if accepted else 'BAD'} "
              f"{entry['models']}  running={ok / i:.0%}")
        client.pause()
    report_log(log_path)


def report_log(log_path):
    rows = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    print(f"\n{len(rows)} logged submissions, accuracy {sum(r['accepted'] for r in rows) / len(rows):.1%}")
    print("threshold  submitted  accuracy-when-submitted")
    for th in (0.0, 0.2, 0.3, 0.35, 0.4, 0.5):
        sub = [r for r in rows if r["conf"] >= th]
        if sub:
            print(f"  {th:4.2f}     {len(sub) / len(rows):6.1%}     {sum(r['accepted'] for r in sub) / len(sub):6.1%}")


def load_labeled():
    items = []
    for folder in ("ok", "labeled"):
        for path in glob.glob(os.path.join(SAMPLES, folder, "*.png")):
            label = os.path.basename(path).split("_")[0]
            if len(label) == 6 and label.isdigit():
                items.append((path, label))
    return items


def bench(only=None):
    items = load_labeled()
    print(f"{len(items)} verified samples\n")
    for name, kwargs in STRATEGIES.items():
        if only and only not in name:
            continue
        solver = DdddOcrSolver(**kwargs)
        t0 = time.time()
        results = []
        for path, label in items:
            with open(path, "rb") as f:
                answer, conf, _ = solver.analyse(f.read())
            results.append((answer == label, conf))
        ms = (time.time() - t0) / max(len(items), 1) * 1000
        acc = sum(r for r, _ in results) / max(len(results), 1)
        line = f"{name:32s} acc={acc:6.1%}  {ms:5.0f} ms/img"
        for th in (0.3, 0.35, 0.4):
            sub = [r for r, c in results if c >= th]
            if sub:
                line += f" | conf>={th}: keeps {len(sub) / len(results):5.1%}, acc {sum(sub) / len(sub):6.1%}"
        print(line)


def build_template(threshold=0.6):
    """Pixels that are black in >= 60% of verified captchas = the fixed, lens-distorted grid
    (digits cover any one pixel far less often than that)."""
    import numpy as np
    from PIL import Image

    from gst_captcha import TEMPLATE_PATH, _ink_mask

    files = [p for p, _ in load_labeled()]
    acc = np.mean([_ink_mask(Image.open(f), False) for f in files], axis=0)
    mask = acc >= threshold
    Image.fromarray(np.where(mask, 0, 255).astype("uint8"), "L").save(TEMPLATE_PATH)
    print(f"Grid template from {len(files)} samples: {mask.mean():.1%} of pixels -> {TEMPLATE_PATH}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="gst_captcha_lab.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--n", type=int, default=100)
    c.add_argument("--gstin", default="03AAFPW4345P1Z7")
    c.add_argument("--delay", type=float, default=0.5)
    b = sub.add_parser("bench")
    b.add_argument("--only", help="Run strategies whose name contains this text")
    sub.add_parser("report")
    sub.add_parser("template")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if args.cmd == "template":
        build_template()
    elif args.cmd == "collect":
        collect(args.n, args.gstin, args.delay)
    elif args.cmd == "bench":
        bench(args.only)
    else:
        report_log(os.path.join(SAMPLES, "collect_log.jsonl"))


if __name__ == "__main__":
    main()
