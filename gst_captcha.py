"""Captcha solvers.

The GST portal captcha is a 6-digit number drawn in black on a 1-px black grid with a red
line through it. The default solver ("ddddocr") uses ddddocr's two neural OCR models, but
decodes their raw per-frame scores itself so that:
  * only digits can be produced (ddddocr's own digit filter drops characters instead),
  * the answer always has exactly 6 digits (length-constrained CTC decoding),
  * several cleaned versions of the image are averaged (test-time augmentation),
  * every answer comes with a confidence, so unsure captchas are skipped (a new image is
    cheap) instead of being submitted and rejected.

Solver API: read(image_bytes, attempt=1, last_chance=False) -> (answer or None, confident: bool)
"""

import io
import logging
import os
import re
import tempfile
import threading

log = logging.getLogger(__name__)

CAPTCHA_LEN = 6
DIGITS = "0123456789"


def clean_guess(text):
    digits = re.sub(r"\D", "", str(text or ""))
    return digits if len(digits) == CAPTCHA_LEN else None


# ------------------------------------------------------------------ image cleaning
def _ink_mask(img, red_as_ink):
    """Pixels that are captcha 'ink' (black, optionally the red line)."""
    import numpy as np

    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    ink = (r < 60) & (g < 60) & (b < 60)
    if red_as_ink:
        ink |= (r > 150) & (g < 90) & (b < 90)
    return ink


def remove_grid(img, red_as_ink=True):
    """Keep ink, drop pixels that are part of 1-px-thick lines (the background grid), smooth."""
    import numpy as np
    from PIL import Image, ImageFilter

    ink = _ink_mask(img, red_as_ink)
    p = np.pad(ink, 1)
    up, down, left, right = p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]
    thin = (~up & ~down) | (~left & ~right)
    keep = ink & ~thin
    out = Image.fromarray(np.where(keep, 0, 255).astype("uint8"), "L")
    return out.filter(ImageFilter.MedianFilter(3))


TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grid_template.png")
_template = None


def grid_template():
    """Boolean mask of the background grid. The portal draws every captcha through the same
    fixed magnifier lens (centre of the image), so the distorted grid is identical in all of
    them; the template is built from verified samples by `captcha_lab template`."""
    global _template
    if _template is None:
        import numpy as np
        from PIL import Image

        _template = np.asarray(Image.open(TEMPLATE_PATH).convert("L")) < 128 if os.path.exists(TEMPLATE_PATH) \
            else False
    return _template


def remove_grid_template(img, red_as_ink=True):
    """Erase the exact (lens-distorted) grid, then refill erased pixels that sit inside a digit
    stroke (most of their non-grid neighbours are ink)."""
    import numpy as np
    from PIL import Image, ImageFilter

    tmpl = grid_template()
    if tmpl is False:
        return remove_grid(img, red_as_ink)
    ink = _ink_mask(img, red_as_ink)
    out = ink & ~tmpl
    p_out, p_t = np.pad(out, 1), np.pad(tmpl, 1)
    H, W = ink.shape
    ink_n = np.zeros((H, W), int)
    known_n = np.zeros((H, W), int)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                ink_n += p_out[1 + dy:H + 1 + dy, 1 + dx:W + 1 + dx]
                known_n += ~p_t[1 + dy:H + 1 + dy, 1 + dx:W + 1 + dx]
    refill = tmpl & ink & (known_n > 0) & (ink_n * 2 > known_n)
    keep = out | refill
    res = Image.fromarray(np.where(keep, 0, 255).astype("uint8"), "L")
    return res.filter(ImageFilter.MedianFilter(3))


VARIANTS = {
    "raw": lambda img: img,
    "clean_red": lambda img: remove_grid(img, True),
    "clean": lambda img: remove_grid(img, False),
    "tmpl_red": lambda img: remove_grid_template(img, True),
    "tmpl": lambda img: remove_grid_template(img, False),
}


# ------------------------------------------------------------------ CTC decoding
def _log_softmax(x):
    import numpy as np

    x = x - x.max(axis=-1, keepdims=True)
    return x - np.log(np.exp(x).sum(axis=-1, keepdims=True))


def decode_fixed_length(logp, length=CAPTCHA_LEN):
    """Most probable CTC path that emits exactly `length` symbols.
    logp: (T, 11) log-probs, column 0 = blank, 1..10 = digits 0-9.
    Returns (text, per-char confidences) or (None, None)."""
    import numpy as np

    T, C = logp.shape
    K = length + 1
    NEG = -1e18
    digits = np.arange(1, C)
    # state: (k emitted so far, current symbol c) ; c=0 blank, 1..10 digit
    score = np.full((K, C), NEG)
    back_k = np.zeros((T, K, C), dtype=np.int8)
    back_c = np.zeros((T, K, C), dtype=np.int8)
    score[0, 0] = logp[0, 0]
    score[1, 1:] = logp[0, 1:]
    ks = np.arange(K)
    for t in range(1, T):
        new = np.full((K, C), NEG)
        bk = np.tile(ks[:, None], (1, C))
        bc = np.zeros((K, C), dtype=np.int64)
        # blank: from anything at the same k
        j = score.argmax(axis=1)
        new[:, 0] = score[ks, j] + logp[t, 0]
        bc[:, 0] = j
        # same digit continues at the same k
        new[:, 1:] = score[:, 1:] + logp[t, 1:]
        bc[:, 1:] = digits
        # new digit c at k: from k-1 in blank or in a different digit
        prev = score[:-1]                                   # (K-1, C)
        dig = prev[:, 1:]
        order = np.argsort(-dig, axis=1)
        top1, top2 = order[:, 0], order[:, 1]
        top1v, top2v = dig[np.arange(K - 1), top1], dig[np.arange(K - 1), top2]
        excl_v = np.where(top1[:, None] == (digits - 1)[None, :], top2v[:, None], top1v[:, None])
        excl_c = np.where(top1[:, None] == (digits - 1)[None, :], top2[:, None], top1[:, None]) + 1
        from_blank = prev[:, 0:1] >= excl_v
        src_v = np.where(from_blank, prev[:, 0:1], excl_v)
        src_c = np.where(from_blank, 0, excl_c)
        emit = src_v + logp[t, 1:]
        better = emit > new[1:, 1:]
        new[1:, 1:] = np.where(better, emit, new[1:, 1:])
        bk[1:, 1:] = np.where(better, ks[:-1, None], bk[1:, 1:])
        bc[1:, 1:] = np.where(better, src_c, bc[1:, 1:])
        new[new < NEG / 2] = NEG
        score = new
        back_k[t], back_c[t] = bk, bc
    k, c = length, int(np.argmax(score[length]))
    if score[k, c] <= NEG / 2:
        return None, None
    # backtrack the frame path
    path = [c]
    for t in range(T - 1, 0, -1):
        k, c = int(back_k[t, k, c]), int(back_c[t, k, c])
        path.append(c)
    path.reverse()
    probs = np.exp(logp)
    text, confs, prev = [], [], 0
    for t, c in enumerate(path):
        if c != 0 and c != prev:
            text.append(DIGITS[c - 1])
            confs.append(float(probs[t, c]))
        elif c != 0 and c == prev:
            confs[-1] = max(confs[-1], float(probs[t, c]))
        prev = c
    return "".join(text), confs


# ------------------------------------------------------------------ models
def disable_power_throttling():
    """Windows 11 'EcoQoS' can park a process that isn't the foreground window on slow efficiency
    cores, making OCR ~10x slower. Opt this process out (no admin rights needed)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class _State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                        ("StateMask", wintypes.ULONG)]

        state = _State(1, 0x1, 0x0)  # EXECUTION_SPEED throttling: controlled, off
        kernel32 = ctypes.windll.kernel32
        kernel32.SetProcessInformation(kernel32.GetCurrentProcess(), 4,  # ProcessPowerThrottling
                                       ctypes.byref(state), ctypes.sizeof(state))
    except Exception as exc:
        log.debug("Could not disable power throttling: %s", exc)


class DigitModel:
    """A ddddocr ONNX model whose output is restricted to blank + digits."""

    def __init__(self, beta=False, norm="v1"):
        import ddddocr

        try:
            ocr = ddddocr.DdddOcr(show_ad=False, beta=beta)
        except TypeError:
            ocr = ddddocr.DdddOcr(beta=beta)
        charset = ocr.get_charset() if hasattr(ocr, "get_charset") else ocr._DdddOcr__charset
        # Own session, no thread spinning. 2 threads measured fastest with 8 workers
        # (0.66 s vs 1.71 s per read with 1 thread). Override with env GST_OCR_THREADS.
        import onnxruntime as ort

        model = os.path.join(os.path.dirname(ddddocr.__file__), "common.onnx" if beta else "common_old.onnx")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = int(os.environ.get("GST_OCR_THREADS", "2"))
        opts.inter_op_num_threads = 1
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(model, opts, providers=["CPUExecutionProvider"])
        del ocr
        self.columns = [0] + [charset.index(d) for d in DIGITS]
        self.input_name = self.session.get_inputs()[0].name
        self.norm = norm
        self.name = ("beta" if beta else "std") + "/" + norm

    def logp(self, img):
        """(T, 11) log-probabilities over blank + digits."""
        import numpy as np
        from PIL import Image

        h = 64
        w = int(img.size[0] * h / img.size[1])
        g = img.resize((w, h), Image.LANCZOS).convert("L")
        x = np.asarray(g, dtype=np.float32)[None, None] / 255.0
        if self.norm == "v1":
            x = (x - 0.5) / 0.5
        out = self.session.run(None, {self.input_name: x})[0]
        if out.ndim == 3:
            out = out[:, 0, :] if out.shape[1] == 1 else out[0]
        return _log_softmax(out[:, self.columns].astype(np.float64))


class CaptchaSolver:
    name = "base"

    def read(self, image, attempt=1, last_chance=False):
        raise NotImplementedError


class DdddOcrSolver(CaptchaSolver):
    """Ensemble of ddddocr models x image variants, averaged per model then combined.
    An answer is 'confident' when both models read the same 6 digits and the better model's
    weakest digit has probability >= threshold. Measured on 100 live portal submissions:
    agreement alone was 97% correct. A 104-GSTIN run at threshold 0.35 had 6 wrong of 110;
    0.5 would have stopped half of those while still passing ~75% of captchas. Skipped captchas
    just cost a fresh image (~0.2 s). Re-tune with `python gst_captcha_lab.py`."""

    name = "ddddocr"

    def __init__(self, models=(("beta", "v1"), ("std", "v1")), variants=("raw", "clean_red", "clean"),
                 threshold=0.5, early_cutoff=0.1):
        disable_power_throttling()
        # beta goes first: it is the stronger model on its own (96% vs 92% on verified samples)
        self.models =[DigitModel(beta=(m == "beta"), norm=n) for m, n in models]
        self.variants = variants
        self.threshold = threshold
        self.early_cutoff = early_cutoff  # first model this unsure -> skip the second, fetch a new image
        # No lock: onnxruntime sessions are safe to run from several threads at once.

    def analyse(self, image, early_exit=False):
        """Returns (answer, confidence 0..1, details)."""
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image)).convert("RGB")
        views = [VARIANTS[v](img) for v in self.variants]
        per_model = []
        for n, model in enumerate(self.models):
            lp = [model.logp(v) for v in views]
            T = min(x.shape[0] for x in lp)
            avg = np.log(np.mean([np.exp(x[:T]) for x in lp], axis=0) + 1e-12)
            text, confs = decode_fixed_length(avg)
            per_model.append((text, min(confs) if confs else 0.0, confs))
            if early_exit and n == 0 and len(self.models) > 1 and per_model[0][1] < self.early_cutoff:
                return text, 0.0, per_model
        texts = {t for t, _, _ in per_model if t}
        if not texts:
            return None, 0.0, per_model
        # confidence = weakest-digit probability of the most confident model; the models
        # must also agree, otherwise the read is treated as unreliable.
        answer, conf, _ = max(per_model, key=lambda r: r[1])
        if len(texts) > 1:
            conf = 0.0
        return answer, conf, per_model

    def read(self, image, attempt=1, last_chance=False):
        answer, conf, detail = self.analyse(image, early_exit=not last_chance)
        log.debug("captcha read %s conf=%.2f %s", answer, conf, [(t, round(c, 2)) for t, c, _ in detail])
        return answer, bool(answer) and conf >= self.threshold


class TesseractSolver(CaptchaSolver):
    name = "tesseract"

    def __init__(self, tesseract_cmd=None):
        import pytesseract

        cmd = tesseract_cmd or os.environ.get("TESSERACT_CMD")
        if not cmd and os.name == "nt":
            default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            cmd = default if os.path.exists(default) else None
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        pytesseract.get_tesseract_version()  # raises if the binary is missing
        self._tess = pytesseract

    def read(self, image, attempt=1, last_chance=False):
        from PIL import Image

        img = remove_grid(Image.open(io.BytesIO(image)))
        img = img.resize((img.width * 3, img.height * 3))
        text = self._tess.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=0123456789")
        guess = clean_guess(text)
        return guess, False  # never confident: only submitted when nothing better is available


class ManualSolver(CaptchaSolver):
    name = "manual"
    _lock = threading.Lock()

    def read(self, image, attempt=1, last_chance=False):
        with self._lock:
            path = os.path.join(tempfile.gettempdir(), "gst_captcha.png")
            with open(path, "wb") as f:
                f.write(image)
            try:
                if os.name == "nt":
                    os.startfile(path)
                else:
                    import webbrowser
                    webbrowser.open("file://" + path)
            except Exception:
                pass
            text = input(f"  Captcha image saved at {path}\n  Type the captcha (blank = new image): ")
        guess = clean_guess(text)
        return guess, bool(guess)


class ChainSolver(CaptchaSolver):
    """Automatic solvers in order; manual entry after `manual_after` rejected attempts."""

    def __init__(self, solvers, manual=None, manual_after=None):
        self.solvers = solvers
        self.manual = manual
        self.manual_after = manual_after
        self.name = "+".join(s.name for s in solvers) or "manual"

    def read(self, image, attempt=1, last_chance=False):
        if self.manual and (not self.solvers or (self.manual_after and attempt > self.manual_after)):
            return self.manual.read(image, attempt, last_chance)
        best = None
        for solver in self.solvers:
            try:
                answer, confident = solver.read(image, attempt, last_chance)
            except Exception as exc:
                log.debug("%s failed: %s", solver.name, exc)
                continue
            if answer and confident:
                return answer, True
            best = best or answer
        return best, False


def build_solver(mode="auto", manual_after=None, tesseract_cmd=None):
    """mode: auto | ddddocr | tesseract | manual."""
    if mode == "manual":
        return ChainSolver([], manual=ManualSolver())

    wanted = ["ddddocr", "tesseract"] if mode == "auto" else [mode]
    solvers = []
    for name in wanted:
        try:
            solvers.append(DdddOcrSolver() if name == "ddddocr" else TesseractSolver(tesseract_cmd))
        except Exception as exc:
            (log.debug if mode == "auto" else log.warning)("Captcha solver '%s' unavailable: %s", name, exc)

    if not solvers and not manual_after:
        raise RuntimeError(
            "No captcha solver available. Run: pip install ddddocr   "
            "(or install Tesseract, or use --solver manual)"
        )
    log.info("Captcha solver: %s", "+".join(s.name for s in solvers) or "manual")
    return ChainSolver(solvers, manual=ManualSolver() if manual_after else None, manual_after=manual_after)
