# Valorent (External Aimbot) — Per-User Build

External, screen-capture based aim assist for Valorant. **Single-file app** (`main.py`) with config-driven tuning and per-build uniqueness tooling.

> **Important:** This project is for educational/engineering discussion of input automation and build hygiene. Use responsibly and comply with all applicable terms, laws, and policies.

## Why this build is designed to be stealthier

| Threat vector | What anti-cheat looks for | How this project addresses it |
|---|---|---|
| File hash/signature scans | Known/static binary hashes and signatures | **Unique per-build output**: obfuscation embeds a per-build `BUILD_SALT` marker (variable length), varies symbol rename lengths, and reorders top-level functions; build scripts print and record SHA256 (`build_hash.txt`) so every artifact differs. |
| Memory read/injection | DLL injection, process memory reads/writes, hooks | **External-only**: captures pixels via `mss` and moves the cursor via Win32 `SendInput` relative deltas. No game process memory access. |
| Behavioral/heuristics | Robotic, perfectly repeatable cursor motion and timing | **Humanize layer**: Gaussian jitter, deadzone to swallow sub-pixel twitch, probabilistic micro-pauses, and variable tick cadence using OS entropy (`random.SystemRandom()` each launch) — not seeded from the build salt. |

## Architecture & safety properties

- **Primary input path:** relative mouse movement with `ctypes` `SendInput` (`MOUSEEVENTF_MOVE`, `dx,dy`). No absolute `SetCursorPos` / absolute `mouseMove` in the primary path.
- **Pure logic helpers:** geometry/color/selection functions are pure and unit-testable headless (`distance`, `clamp01`, `lerp`, `smooth_step`, `center_of_mass`, `select_nearest`, `hex_to_rgb`, `color_mask`).
- **Config-driven:** all tunables live in `config.yaml` (including `calibration.region_size`).
- **Threading/GUI:** aim loop runs in a daemon thread; tkinter GUI stays responsive; RGB target updates are lock-guarded; thread stops on window close.

## Project layout

- `main.py` — single-file engine + GUI
- `config.yaml` — tunables (detection, calibration, hotkeys, humanize, capture)
- `tests/test_logic.py` — headless unit/integration-style logic tests
- `build/obfuscate.py` — AST-based obfuscation, embedded salt marker, per-build hash
- `build/build_exe.bat` — local unique build pipeline
- `.github/workflows/release.yml` — CI build/release pipeline (unique per run)
- `requirements.txt`, `README.md`

## Tuning table (`config.yaml`)

| Key | Description | Example |
|---|---|---|
| `general.fire_key` | Hold-to-activate key (config-defined) | `"right_click"` |
| `general.toggle_key` | Pause/resume hotkey | `"f5"` |
| `general.calibrate_key` | Calibrate hotkey (samples color at crosshair) | `"f6"` |
| `detection.skin_color_mode` | `auto` (use calibration) or `manual` | `"auto"` |
| `detection.skin_color_manual` | Manual target color (hex) | `"#C9976F"` |
| `detection.skin_tolerance` | Per-channel RGB tolerance | `24` |
| `detection.head_min_area` | Minimum matched pixels to count as a target/head | `12` |
| `detection.scan_region_pct_x/y` | Center scan band as fraction of screen | `0.40` / `0.40` |
| `behavior.smoothness` | Movement smoothing (`0` = snappy, `1` = gentle glide) | `0.65` |
| `behavior.max_speed_px` | Per-frame movement cap (px) | `450` |
| `behavior.y_priority` | Prefer kill-shot vertical band in target selection | `true` |
| `behavior.use_pydirectinput` | `false` = relative SendInput primary (stealthier), `true` = pydirectinput fallback path | `false` |
| `humanize.enabled` | Enable humanization layer | `true` |
| `humanize.jitter_px` | Gaussian jitter sigma per step (px) | `2.5` |
| `humanize.micro_pause_chance` | Per-tick chance of micro-pause | `0.08` |
| `humanize.micro_pause_ms` | Micro-pause duration bounds `[min,max]` | `[4, 16]` |
| `humanize.tick_jitter_pct` | Variable cadence jitter factor | `0.25` |
| `humanize.deadzone_px` | Deadzone to swallow sub-pixel twitch | `1.0` |
| `performance.scan_fps` | Target scan/update cadence | `60` |
| `performance.capture_scale` | Capture scaling factor | `1.0` |
| `calibration.region_size` | **Square size (px)** sampled at screen center during calibration | `40` |
| `identity.auto_salt` | Generate/persist a per-install salt if none set (runtime identity) | `true` |

## Presets

### Stealth (recommended baseline)
```yaml
behavior:
  smoothness: 0.75
  max_speed_px: 320
  y_priority: true
  use_pydirectinput: false
humanize:
  enabled: true
  jitter_px: 2.8
  micro_pause_chance: 0.10
  micro_pause_ms: [6, 18]
  tick_jitter_pct: 0.30
  deadzone_px: 1.2
performance:
  scan_fps: 60
detection:
  skin_tolerance: 22
  head_min_area: 14
```

### Aggressive
```yaml
behavior:
  smoothness: 0.45
  max_speed_px: 650
  y_priority: true
  use_pydirectinput: false
humanize:
  enabled: true
  jitter_px: 1.6
  micro_pause_chance: 0.04
  micro_pause_ms: [3, 10]
  tick_jitter_pct: 0.15
  deadzone_px: 0.6
performance:
  scan_fps: 90
detection:
  skin_tolerance: 28
  head_min_area: 10
```

Apply by copying the desired values into `config.yaml` (or maintain `config_stealth.yaml` / `config_aggressive.yaml` locally and swap).

## Run

```bash
pip install -r requirements.txt
python main.py
```

**Hotkeys/UI**
- `F5` — pause/resume
- `F6` — calibrate (samples `calibration.region_size` box at screen center)
- Buttons: Pause, Calibrate, Reset, Quit
- Fire key: from `hotkeys.fire` in config

## Test

```bash
pytest tests/ -q
```

Tests are headless and cover geometry/color helpers, nearest-target y-priority, humanize deadzone/cadence bounds, build-id stability/env override, obfuscation uniqueness, and a synthetic-head end-to-end detection path.

## Build (unique per run)

### Windows (local)
```bat
build\build_exe.bat
```
What it does:
1. Runs `build/obfuscate.py` to generate a per-build obfuscated `__obfuscated/main.py` (embedded `BUILD_SALT`, length-varying renames, function reordering) and writes `__obfuscated/build_hash.txt`.
2. Sets `VALORENT_BUILD_SALT` from the fresh salt.
3. Builds with PyInstaller: `--onefile --windowed --name Valorent` on the **obfuscated** source.
4. Prints SHA256 of `dist\Valorent.exe` and writes `build_hash.txt`.

Running the obfuscation/build twice produces different hashes (and different embedded salt markers).

### CI
`.github/workflows/release.yml` mirrors the same flow so each workflow run/artifact is unique.

## Notes
- Requires Windows APIs for `SendInput`/GUI runtime behavior; core logic tests run headless.
- Keep builds external-only (no memory injection/read-write of the game process).
