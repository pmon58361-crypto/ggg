"""
Valorent Aimbot (Stealth Upgrade)
External, single-file app: screen capture via mss, relative SendInput mouse deltas,
config-driven tuning, per-build embedded salt marker for unique obfuscated builds.
"""
from __future__ import annotations

import ctypes
import hashlib
import math
import os
import random
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

try:
    import mss  # type: ignore
except Exception:  # pragma: no cover
    mss = None

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None

try:
    import keyboard  # type: ignore
except Exception:  # pragma: no cover
    keyboard = None

try:
    import pydirectinput  # type: ignore
except Exception:  # pragma: no cover
    pydirectinput = None

CFG_PATH = Path(__file__).parent / "config.yaml"

# --- Embedded build marker (replaced/varied per obfuscated build; R3) ---
BUILD_SALT = "0x" + secrets.token_hex(16)
_SALT_ENV = "VALORENT_BUILD_SALT"
_BUILD_MARKER = BUILD_SALT  # marker token present in source bytes


# =========================
# Pure helper functions (R5)
# =========================

def _is_point(v: Any) -> bool:
    return isinstance(v, (tuple, list)) and len(v) == 2


def distance(a, b=None, c=None, d=None) -> float:
    # Supports distance((x1,y1),(x2,y2)) and distance(x1,y1,x2,y2)
    if b is not None and c is None and d is None and _is_point(a) and _is_point(b):
        x1, y1 = float(a[0]), float(a[1])
        x2, y2 = float(b[0]), float(b[1])
        return math.hypot(x2 - x1, y2 - y1)
    if c is not None and d is not None:
        x1, y1, x2, y2 = float(a), float(b), float(c), float(d)
        return math.hypot(x2 - x1, y2 - y1)
    raise TypeError("distance expects (p1,p2) or (x1,y1,x2,y2)")


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def lerp(a: float, b: float, t: float) -> float:
    return float(a) + (float(b) - float(a)) * clamp01(t)


def smooth_step(a, b=None, c=None):
    # Supports smooth_step(t) -> eased t and smooth_step(current,target,strength)
    if b is None and c is None:
        t = clamp01(a)
        return t * t * (3.0 - 2.0 * t)
    current, target, strength = float(a), float(b), float(c)
    return current + (target - current) * clamp01(strength)


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {h}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def center_of_mass(data):
    """Centroid of True pixels in mask/ndarray, or of a list of (x,y) points."""
    if np is not None and hasattr(data, "shape") and not isinstance(data, (list, tuple)):
        ys, xs = np.nonzero(data)
        if getattr(xs, "size", 0) == 0:
            return None
        return float(xs.mean()), float(ys.mean())
    points = list(data) if data is not None else []
    if not points:
        return None
    sx = 0.0
    sy = 0.0
    for p in points:
        sx += float(p[0]); sy += float(p[1])
    n = float(len(points))
    return (sx / n, sy / n)


def select_nearest(points, a, b=None, y_priority: Union[bool, float] = True):
    """Supports select_nearest(points, origin, y_priority=float) and
    select_nearest(points, cx, cy, y_priority=bool)."""
    if b is None and _is_point(a):
        origin = (float(a[0]), float(a[1]))
        cx, cy = origin
        yp = float(y_priority) if not isinstance(y_priority, bool) else (1.0 if y_priority else 0.0)
        use_weighted = True
    else:
        cx, cy = float(a), float(b)
        yp = 1.0 if bool(y_priority) else 0.0
        use_weighted = isinstance(y_priority, bool)
    pts = list(points) if points is not None else []
    if not pts:
        return None
    best = None
    best_score = float("inf")
    for p in pts:
        px, py = float(p[0]), float(p[1])
        dx = px - cx
        dy = py - cy
        if use_weighted and isinstance(y_priority, bool) and y_priority:
            score = (abs(dy) ** 2) * 1.8 + (abs(dx) ** 2)
        elif use_weighted and isinstance(y_priority, bool) and not y_priority:
            score = dx * dx + dy * dy
        else:
            # float y_priority: distance + yp*|dy| (test-compatible)
            score = math.hypot(dx, dy) + float(yp) * abs(dy)
        if score < best_score:
            best_score = score
            best = p
    return best


def color_mask(data, rgb_or_target, tol: int = 0):
    """If data is an RGB pixel tuple/list -> bool. If ndarray region -> boolean mask."""
    if np is not None and hasattr(data, "shape") and not isinstance(data, (tuple, list)):
        region = data
        r, g, b = rgb_or_target
        diff = np.abs(region.astype(np.int16) - np.array([r, g, b], dtype=np.int16))
        return np.all(diff <= int(tol), axis=-1)
    # pixel mode
    pix = data
    target = rgb_or_target
    return (
        abs(int(pix[0]) - int(target[0])) <= tol
        and abs(int(pix[1]) - int(target[1])) <= tol
        and abs(int(pix[2]) - int(target[2])) <= tol
    )


# =========================
# Build / config utilities
# =========================

def _persist_install_salt(cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg or {}
    ident = cfg.get("identity", {}) if isinstance(cfg.get("identity", {}), dict) else {}
    if not ident.get("auto_salt", False):
        return ""
    path = Path(__file__).parent / ".valorent_salt"
    try:
        if path.exists():
            val = path.read_text(encoding="utf-8").strip()
            if val:
                return val
        val = "0x" + secrets.token_hex(16)
        path.write_text(val, encoding="utf-8")
        return val
    except Exception:
        return ""


def get_build_id(env: Optional[Dict[str, str]] = None, cfg: Optional[Dict[str, Any]] = None) -> str:
    env = os.environ if env is None else env
    override = env.get(_SALT_ENV)
    if override:
        salt = override
    else:
        salt = BUILD_SALT
        inst = _persist_install_salt(cfg or {})
        if inst:
            salt = salt + ":" + inst
    return hashlib.sha256(salt.encode("utf-8")).hexdigest()[:12]


BUILD_ID = get_build_id()


def load_config(path: Union[str, Path] = CFG_PATH) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML is required to load config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# =========================
# Humanizer (R4)
# =========================

class Humanizer:
    def __init__(self, cfg: Dict[str, Any], rng: Optional[random.Random] = None):
        cfg = cfg or {}
        hcfg = cfg.get("humanize", {}) if isinstance(cfg.get("humanize", {}), dict) else {}
        beh = cfg.get("behavior", {}) if isinstance(cfg.get("behavior", {}), dict) else {}
        perf = cfg.get("performance", {}) if isinstance(cfg.get("performance", {}), dict) else {}

        self.enabled = bool(hcfg.get("enabled", True))
        self.jitter_px = float(hcfg.get("jitter_px", hcfg.get("jitter", 0.6)))
        self.deadzone_px = float(hcfg.get("deadzone_px", hcfg.get("deadzone", 0.35)))

        if "micro_pause_chance" in hcfg or "micro_pause_ms" in hcfg:
            self.pause_chance = float(hcfg.get("micro_pause_chance", hcfg.get("pause_chance", 0.04)))
            mp = hcfg.get("micro_pause_ms", [hcfg.get("pause_min_ms", 8.0), hcfg.get("pause_max_ms", 28.0)])
            if isinstance(mp, (list, tuple)) and len(mp) == 2:
                self.pause_min_ms = float(mp[0]); self.pause_max_ms = float(mp[1])
            else:
                self.pause_min_ms = float(hcfg.get("pause_min_ms", 8.0)); self.pause_max_ms = float(hcfg.get("pause_max_ms", 28.0))
        else:
            self.pause_chance = float(hcfg.get("pause_chance", 0.04))
            self.pause_min_ms = float(hcfg.get("pause_min_ms", 8.0))
            self.pause_max_ms = float(hcfg.get("pause_max_ms", 28.0))

        self.tick_jitter = float(hcfg.get("tick_jitter_pct", hcfg.get("tick_jitter", 0.25)))
        if "base_tick_ms" in hcfg:
            self.base_tick_ms = float(hcfg.get("base_tick_ms", 8.0))
        else:
            scan_fps = max(1.0, float(perf.get("scan_fps", 60.0) or 60.0))
            self.base_tick_ms = 1000.0 / scan_fps

        if "speed" in hcfg:
            self.speed = float(hcfg.get("speed", 0.35))
        else:
            smoothness = clamp01(float(beh.get("smoothness", 0.65)))
            self.speed = max(0.05, min(1.0, 1.0 - 0.75 * smoothness))

        # R4: OS entropy each launch, not seeded from build salt
        self.rng = rng if rng is not None else random.SystemRandom()

    # API compatibility aliases
    @property
    def jitter(self):
        return self.jitter_px
    @property
    def deadzone(self):
        return self.deadzone_px

    def _gauss(self, sigma: float) -> float:
        if sigma <= 0:
            return 0.0
        u1 = max(self.rng.random(), 1e-12)
        u2 = self.rng.random()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2) * sigma

    def apply_deadzone(self, dx: float, dy: float) -> Tuple[float, float]:
        if abs(dx) < self.deadzone_px:
            dx = 0.0
        if abs(dy) < self.deadzone_px:
            dy = 0.0
        return dx, dy

    def perturb(self, dx: float, dy: float) -> Tuple[float, float]:
        if not self.enabled:
            return dx, dy
        dx += self._gauss(self.jitter_px)
        dy += self._gauss(self.jitter_px)
        if math.hypot(dx, dy) < self.deadzone_px:
            return 0.0, 0.0
        return dx, dy

    def maybe_pause(self) -> float:
        if not self.enabled:
            return 0.0
        if self.rng.random() < self.pause_chance:
            span = max(0.0, self.pause_max_ms - self.pause_min_ms)
            return (self.pause_min_ms + self.rng.random() * span) / 1000.0
        return 0.0

    def pause_duration(self) -> float:
        return self.maybe_pause()

    def next_tick_delay(self) -> float:
        factor = 1.0 + (self.rng.random() * 2.0 - 1.0) * self.tick_jitter
        factor = max(0.2, factor)
        return max(0.001, (self.base_tick_ms / 1000.0) * factor)

    def tick_interval(self, base_dt: float) -> float:
        if not self.enabled:
            return base_dt
        return base_dt * (1.0 + (self.rng.random() * 2.0 - 1.0) * self.tick_jitter)

    def step(self, target_dx: float, target_dy: float) -> Tuple[int, int, float]:
        if not self.enabled:
            return (int(round(target_dx)), int(round(target_dy)), 0.0)
        dx = float(target_dx) * self.speed
        dy = float(target_dy) * self.speed
        dx, dy = self.perturb(dx, dy)
        dx, dy = self.apply_deadzone(dx, dy)
        pause_s = self.maybe_pause()
        return (int(round(dx)), int(round(dy)), pause_s)


# =========================
# Input / movement (R1,R2)
# =========================

class Mover:
    MOUSEEVENTF_MOVE = 0x0001

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or {}
        beh = self.cfg.get("behavior", {}) if isinstance(self.cfg.get("behavior", {}), dict) else {}
        self.use_pydirectinput = bool(beh.get("use_pydirectinput", False))

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("i",)
            _fields_ = [("type", ctypes.c_ulong), ("i", _INPUTUNION)]

        self._MOUSEINPUT = MOUSEINPUT
        self._INPUT = INPUT

    def _send_relative(self, dx: int, dy: int) -> bool:
        dx_i, dy_i = int(round(dx)), int(round(dy))
        if dx_i == 0 and dy_i == 0:
            return True
        try:
            extra = ctypes.c_ulong(0)
            mi = self._MOUSEINPUT(dx_i, dy_i, 0, self.MOUSEEVENTF_MOVE, 0, ctypes.pointer(extra))
            inp = self._INPUT(0, mi=mi)
            sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
            return sent == 1
        except Exception:
            return False

    def move_relative(self, dx: float, dy: float) -> None:
        dx_i, dy_i = int(round(dx)), int(round(dy))
        # R1/R2: primary path = relative SendInput with dx,dy
        if self.use_pydirectinput and pydirectinput is not None:
            try:
                pydirectinput.moveRel(dx_i, dy_i, relative=True)
                return
            except Exception:
                pass
        ok = self._send_relative(dx_i, dy_i)
        if not ok and pydirectinput is not None:
            try:
                pydirectinput.moveRel(dx_i, dy_i, relative=True)
            except Exception:
                pass

    def _apply_move(self, dx: float, dy: float) -> None:
        self.move_relative(dx, dy)


# =========================
# Detection
# =========================

class Detector:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        det = self.cfg.get("detection", {}) if isinstance(self.cfg.get("detection", {}), dict) else {}
        cal = self.cfg.get("calibration", {}) if isinstance(self.cfg.get("calibration", {}), dict) else {}
        beh = self.cfg.get("behavior", {}) if isinstance(self.cfg.get("behavior", {}), dict) else {}

        manual_hex = det.get("skin_color_manual", det.get("target_hex", "#C9976F"))
        self.target_hex = str(manual_hex)
        self.tolerance = int(det.get("skin_tolerance", det.get("tolerance", 30)))
        self.min_pixels = int(det.get("head_min_area", det.get("min_pixels", 6)))
        self.scan_region_pct_x = float(det.get("scan_region_pct_x", 1.0) or 1.0)
        self.scan_region_pct_y = float(det.get("scan_region_pct_y", 1.0) or 1.0)

        yp = beh.get("y_priority", det.get("y_priority", 0.0))
        self.y_priority = (1.0 if yp else 0.0) if isinstance(yp, bool) else float(yp or 0.0)

        self.target_rgb = hex_to_rgb(self.target_hex)
        self.region_size = int(cal.get("region_size", 8))

    def set_target_rgb(self, rgb: Tuple[int, int, int]) -> None:
        self.target_rgb = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        self.target_hex = "#{:02X}{:02X}{:02X}".format(*self.target_rgb)

    @property
    def rgb(self) -> Tuple[int, int, int]:
        return self.target_rgb

    def _iter_matches(self, img) -> List[Tuple[int, int]]:
        matches: List[Tuple[int, int]] = []
        w = getattr(img, "width", 0)
        h = getattr(img, "height", 0)
        raw_rgb = getattr(img, "rgb", None)
        if raw_rgb is not None:
            idx = 0
            for y in range(h):
                for x in range(w):
                    r = raw_rgb[idx]; g = raw_rgb[idx + 1]; b = raw_rgb[idx + 2]
                    idx += 3
                    if color_mask((r, g, b), self.target_rgb, self.tolerance):
                        matches.append((x, y))
            return matches
        bgra = getattr(img, "pixels", None) or getattr(img, "raw", None)
        if bgra is not None:
            idx = 0
            for y in range(h):
                for x in range(w):
                    b = bgra[idx]; g = bgra[idx + 1]; r = bgra[idx + 2]
                    idx += 4
                    if color_mask((r, g, b), self.target_rgb, self.tolerance):
                        matches.append((x, y))
        return matches

    def detect(self, img, origin: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        matches = self._iter_matches(img)
        if len(matches) < self.min_pixels:
            return None
        pts = [(float(x), float(y)) for x, y in matches]
        return select_nearest(pts, origin, y_priority=self.y_priority)

    def detect_head_center(self, img, origin: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        matches = self._iter_matches(img)
        if len(matches) < self.min_pixels:
            return None
        return center_of_mass(matches)


# =========================
# Engine
# =========================

class AimbotEngine:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self.detector = Detector(self.cfg)
        self.humanizer = Humanizer(self.cfg)
        self.mover = Mover(self.cfg)
        self._rgb_lock = threading.Lock()
        self.running = False
        self.paused = False
        self._thread: Optional[threading.Thread] = None

        perf = self.cfg.get("performance", {}) if isinstance(self.cfg.get("performance", {}), dict) else {}
        det = self.cfg.get("detection", {}) if isinstance(self.cfg.get("detection", {}), dict) else {}
        beh = self.cfg.get("behavior", {}) if isinstance(self.cfg.get("behavior", {}), dict) else {}
        self.scan_fps = max(1.0, float(perf.get("scan_fps", 60.0) or 60.0))
        self.capture_scale = max(0.1, float(perf.get("capture_scale", 1.0) or 1.0))
        self.scan_region_pct_x = float(det.get("scan_region_pct_x", 1.0) or 1.0)
        self.scan_region_pct_y = float(det.get("scan_region_pct_y", 1.0) or 1.0)
        self.max_speed_px = float(beh.get("max_speed_px", 450.0) or 450.0)

    @property
    def build_id(self) -> str:
        return get_build_id(cfg=self.cfg)

    def set_target_rgb_threadsafe(self, rgb: Tuple[int, int, int]) -> None:
        with self._rgb_lock:
            self.detector.set_target_rgb(rgb)

    def get_target_rgb_threadsafe(self) -> Tuple[int, int, int]:
        with self._rgb_lock:
            return self.detector.target_rgb

    def _scan_bbox(self, monitor: Dict[str, int]) -> Dict[str, int]:
        mw = int(monitor["width"]); mh = int(monitor["height"])
        w = max(16, int(mw * max(0.05, min(1.0, self.scan_region_pct_x)) * self.capture_scale))
        h = max(16, int(mh * max(0.05, min(1.0, self.scan_region_pct_y)) * self.capture_scale))
        cx = int(monitor["left"] + mw // 2); cy = int(monitor["top"] + mh // 2)
        return {"left": cx - w // 2, "top": cy - h // 2, "width": w, "height": h}

    def capture_region(self):
        if mss is None:
            return None
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            return sct.grab(self._scan_bbox(monitor))

    def calibrate(self):
        # R6/B6: respect calibration.region_size (not hardcoded)
        if mss is None:
            return False
        cal = self.cfg.get("calibration", {}) if isinstance(self.cfg.get("calibration", {}), dict) else {}
        region_size = int(cal.get("region_size", 8))
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            cx = monitor["left"] + monitor["width"] // 2
            cy = monitor["top"] + monitor["height"] // 2
            half = max(1, region_size) // 2
            bbox = {"left": cx - half, "top": cy - half, "width": max(1, half * 2), "height": max(1, half * 2)}
            shot = sct.grab(bbox)
            raw = shot.raw  # BGRA
            if not raw:
                return False
            n = len(raw) // 4
            if n <= 0:
                return False
            rs = gs = bs = 0
            for i in range(0, len(raw), 4):
                bs += raw[i]; gs += raw[i + 1]; rs += raw[i + 2]
            self.set_target_rgb_threadsafe((rs // n, gs // n, bs // n))
            return True

    def _cap_speed(self, dx: int, dy: int) -> Tuple[int, int]:
        mag = math.hypot(dx, dy)
        if mag <= 0:
            return dx, dy
        max_step = max(1.0, self.max_speed_px / max(1.0, self.scan_fps))
        if mag > max_step:
            s = max_step / mag
            return int(round(dx * s)), int(round(dy * s))
        return dx, dy

    def aim_once(self) -> bool:
        shot = self.capture_region()
        if shot is None:
            return False
        origin = (shot.width / 2.0, shot.height / 2.0)
        with self._rgb_lock:
            target = self.detector.detect_head_center(shot, origin) or self.detector.detect(shot, origin)
        if target is None:
            return False
        dx = target[0] - origin[0]
        dy = target[1] - origin[1]
        mx, my, pause_s = self.humanizer.step(dx, dy)
        mx, my = self._cap_speed(int(mx), int(my))
        if pause_s > 0:
            time.sleep(pause_s)
        self.mover._apply_move(mx, my)
        return True

    def _loop(self):
        while self.running:
            try:
                if not self.paused:
                    self.aim_once()
                delay = min(self.humanizer.next_tick_delay(), 1.0 / max(1.0, self.scan_fps))
                time.sleep(max(0.001, delay))
            except Exception:
                time.sleep(0.01)

    def start(self):
        if self.running:
            return
        self.running = True
        self.paused = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def reset(self):
        self.paused = False

    def toggle_pause(self):
        self.paused = not self.paused


# =========================
# GUI (R7)
# =========================

def run_gui(cfg: Dict[str, Any]):  # pragma: no cover
    import tkinter as tk

    engine = AimbotEngine(cfg)
    root = tk.Tk()
    root.title("Valorent")
    root.configure(bg="#0d1117")
    accent = "#ff4655"
    fg = "#e6edf3"

    status_var = tk.StringVar(value="Ready")
    build_var = tk.StringVar(value=f"Build: {engine.build_id}")
    calib_var = tk.StringVar(value="Calibration: idle")

    def set_status(msg: str):
        status_var.set(msg)

    frame = tk.Frame(root, bg="#0d1117")
    frame.pack(padx=16, pady=16, fill="both", expand=True)

    tk.Label(frame, text="Valorent Aimbot", bg="#0d1117", fg=accent, font=("Segoe UI", 16, "bold")).pack(anchor="w")
    tk.Label(frame, textvariable=build_var, bg="#0d1117", fg=fg).pack(anchor="w", pady=(4, 2))
    tk.Label(frame, textvariable=calib_var, bg="#0d1117", fg=fg).pack(anchor="w", pady=(0, 8))
    tk.Label(frame, textvariable=status_var, bg="#0d1117", fg=fg).pack(anchor="w", pady=(0, 12))

    btns = tk.Frame(frame, bg="#0d1117")
    btns.pack(anchor="w")

    def on_pause():
        engine.toggle_pause()
        set_status("Paused" if engine.paused else "Running")

    def on_calibrate():
        ok = engine.calibrate()
        if ok:
            rgb = engine.get_target_rgb_threadsafe()
            calib_var.set(f"Calibration: #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}")
            set_status("Calibrated")
        else:
            set_status("Calibration failed")

    def on_reset():
        engine.reset()
        calib_var.set("Calibration: idle")
        set_status("Reset")

    def on_quit():
        engine.stop()
        root.destroy()

    def mkbtn(text, cmd):
        return tk.Button(btns, text=text, command=cmd, bg="#161b22", fg=fg, activebackground=accent, relief="flat", padx=10, pady=6)

    mkbtn("Pause", on_pause).pack(side="left", padx=(0, 6))
    mkbtn("Calibrate", on_calibrate).pack(side="left", padx=6)
    mkbtn("Reset", on_reset).pack(side="left", padx=6)
    mkbtn("Quit", on_quit).pack(side="left", padx=6)

    general = cfg.get("general", {}) if isinstance(cfg.get("general", {}), dict) else {}
    hotkeys = cfg.get("hotkeys", {}) if isinstance(cfg.get("hotkeys", {}), dict) else {}
    fire_key = str(general.get("fire_key", hotkeys.get("fire", "right_click"))).lower()
    toggle_key = str(general.get("toggle_key", hotkeys.get("pause", "f5"))).lower()
    calibrate_key = str(general.get("calibrate_key", hotkeys.get("calibrate", "f6"))).lower()

    root.bind("<F5>", lambda _e=None: on_pause())
    root.bind("<F6>", lambda _e=None: on_calibrate())

    # Optional global hotkeys if keyboard module is available
    if keyboard is not None:
        try:
            if toggle_key:
                keyboard.add_hotkey(toggle_key, lambda: root.after(0, on_pause))
            if calibrate_key:
                keyboard.add_hotkey(calibrate_key, lambda: root.after(0, on_calibrate))
        except Exception:
            pass

    engine.start()
    set_status(f"Running (fire: {fire_key})")

    def on_close():
        engine.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def main():  # pragma: no cover
    cfg = load_config(CFG_PATH)
    run_gui(cfg)


if __name__ == "__main__":  # pragma: no cover
    main()
