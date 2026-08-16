#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow", "onnxruntime"]
# ///
"""quicksnap — grab a usable frame off a webcam, for a vision model to read.

The naive `ffmpeg -frames:v 1` grabs frame #1, before the camera's autofocus /
auto-exposure / auto-white-balance have converged. On the reference rig that frame
scores 15.0 on variance-of-Laplacian; frame #4 scores 109.5. Everything else in this
script is a rounding error next to throwing away the first few frames.

See README.md for the measurements behind each default.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# Claude downscales images to fit ~1568px on the long edge, so anything bigger is
# encoded, uploaded and then thrown away. Upscaling only pays off after a --crop.
CLAUDE_LONG_EDGE = 1568

MODEL_URL = (
    "https://huggingface.co/Kim2091/UltraSharp/resolve/main/"
    "ONNX/4x-UltraSharp-fp16-opset17.onnx"
)
MODEL_SCALE = 4
CACHE_DIR = Path.home() / ".cache" / "quicksnap"

# radius / amount / threshold for unsharp mask, plus levels aggressiveness.
PRESETS = {
    # Sane middle ground.
    "general": {"usm": (1.2, 1.0, 3), "lo_pct": 0.5, "hi_pct": 99.7, "knee": 0.85},
    # Displays: gentle sharpening so we don't amplify moire off the pixel grid,
    # and a low knee because bright panels blow out easily.
    "screen": {"usm": (1.0, 0.7, 4), "lo_pct": 0.5, "hi_pct": 99.5, "knee": 0.75},
    # PCBs / breadboards: detail matters, and so does colour (resistor bands),
    # so keep the levels stretch conservative.
    "board": {"usm": (1.5, 1.2, 2), "lo_pct": 0.3, "hi_pct": 99.8, "knee": 0.88},
    # Silkscreen, part numbers, datasheet pages: push local contrast hard.
    "text": {"usm": (1.5, 1.6, 2), "lo_pct": 1.0, "hi_pct": 99.5, "knee": 0.9},
}


# --------------------------------------------------------------------------- utils


def log(msg: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


def die(msg: str) -> "None":
    print(f"quicksnap: {msg}", file=sys.stderr)
    raise SystemExit(1)


def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian. Higher is sharper.

    Only meaningful for comparing frames of the *same* scene at the same JPEG
    quality -- compression noise inflates it, which is why capture pins -q:v 2.
    """
    lap = (
        4.0 * gray[1:-1, 1:-1]
        - gray[:-2, 1:-1]
        - gray[2:, 1:-1]
        - gray[1:-1, :-2]
        - gray[1:-1, 2:]
    )
    return float(lap.var())


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


def content_mask(rgb: np.ndarray) -> np.ndarray:
    """Pixels worth estimating colour from: not crushed black, not blown white.

    Matters for mixed-illuminant scenes. On the reference shot a dim red-lit room
    surrounds a bright bluish screen; estimating over the whole frame lets the dead
    surround drag the illuminant estimate somewhere useless.
    """
    luma = rgb.mean(axis=2)
    m = (luma > 0.05) & (luma < 0.98)
    return m if m.sum() > 0.02 * luma.size else np.ones_like(m, dtype=bool)


# ------------------------------------------------------------------------- capture


def list_devices() -> str:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True,
        text=True,
    )
    lines = [
        re.sub(r"^\[[^\]]*\]\s*", "", ln)
        for ln in proc.stderr.splitlines()
        if "AVFoundation" in ln or re.search(r"^\[[^\]]*\]\s*\[\d+\]", ln)
    ]
    return "\n".join(lines)


def capture_frames(device: str, size: str, fps: int, count: int, outdir: Path,
                   pixel_format: str | None, quiet: bool) -> list[Path]:
    """Grab `count` frames in ONE ffmpeg invocation.

    Two things here are load-bearing:

    * One invocation. Closing and reopening the device resets 3A convergence, so
      warm-up frames and the burst have to come from the same open handle.
    * -fps_mode passthrough. Without it ffmpeg pads the camera's real ~5fps up to
      the requested framerate by *duplicating* frames, and the "burst" turns into N
      identical copies of one pre-convergence frame.
    """
    cmd = ["ffmpeg", "-hide_banner", "-y", "-v", "error", "-f", "avfoundation",
           "-framerate", str(fps), "-video_size", size]
    if pixel_format:
        cmd += ["-pixel_format", pixel_format]
    cmd += ["-i", device, "-frames:v", str(count), "-fps_mode", "passthrough",
            "-q:v", "2", str(outdir / "f_%04d.jpg")]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    frames = sorted(outdir.glob("f_*.jpg"))
    if not frames:
        detail = proc.stderr.strip() or "no frames written"
        die(f"capture failed from device {device!r}\n{detail}")
    if len(frames) < count:
        log(f"  note: asked for {count} frames, got {len(frames)}", quiet=quiet)
    return frames


def find_stable_start(scores: list[float], lumas: list[float]) -> int:
    """Index of the first frame that already looks like the converged end state.

    Do NOT just take the sharpest frame. 3A converges *towards* a steady state, so
    the steady state is the tail, and an early frame can outscore it for the wrong
    reasons -- a high-gain frame is noisy, and a mid-hunt frame can be blown out and
    high-contrast. Both inflate variance-of-Laplacian. Observed in the wild: a burst
    scoring [20, 246, 177, 177, 133, ..., 126] where frame 2 was overexposed and
    grainy, and every frame after 9 was correct.

    So: anchor on the last few frames, then walk backwards while frames still match
    that steady state in both exposure and detail.
    """
    n = len(scores)
    if n <= 2:
        return 0

    ref_n = min(3, n)
    s_ref = float(np.median(scores[-ref_n:]))
    l_ref = float(np.median(lumas[-ref_n:]))

    start = n - ref_n
    while start > 0:
        i = start - 1
        s_ok = abs(scores[i] - s_ref) <= 0.20 * max(s_ref, 1e-6)
        l_ok = abs(lumas[i] - l_ref) <= 0.05 * max(l_ref, 1e-6)
        if not (s_ok and l_ok):
            break
        start = i

    # Frame 1 is essentially never usable; never hand it back from auto mode.
    return max(start, 1)


def pick_frame(frames: list[Path], warmup: str | int, quiet: bool):
    """Score every captured frame, drop the ones still converging, keep the sharpest.

    Returns (chosen_path, scores, warmup_used, chosen_index).
    """
    scores, lumas = [], []
    for f in frames:
        g = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
        scores.append(sharpness(g))
        lumas.append(float(g.mean()))

    if warmup == "auto":
        start = find_stable_start(scores, lumas)
    else:
        start = min(int(warmup), len(frames) - 1)

    best = max(range(start, len(frames)), key=lambda i: scores[i])
    log(f"  [lucky]    frame {best + 1}/{len(frames)}  "
        f"(warm-up {start}, sharpness {scores[best]:.1f})", quiet=quiet)
    return frames[best], scores, start, best


# ---------------------------------------------------------------------- processing


def white_balance(rgb: np.ndarray, mode: str, mask: np.ndarray):
    """Estimate the illuminant and divide it out. Returns (image, gains).

    Done in linear light -- gains applied straight to sRGB values skew hue, which
    matters when the thing being read is a resistor colour band or an LED.
    """
    if mode == "none":
        return rgb, np.ones(3)

    px = rgb[mask]
    if mode == "gray":  # grey-world: p=1
        ill = px.mean(axis=0)
    elif mode == "whitepatch":  # p -> infinity
        ill = np.percentile(px, 97, axis=0)
    else:  # "shades": Minkowski p=6, sits between the two and handles mixed light
        p = 6
        ill = np.power(np.power(px, p).mean(axis=0), 1 / p)

    ill = np.maximum(ill, 1e-6)
    ill = ill / ill.max()
    gains = 1.0 / ill

    lin = srgb_to_linear(rgb) * gains
    return linear_to_srgb(lin), gains


def stretch_levels(rgb: np.ndarray, mask: np.ndarray, lo_pct: float, hi_pct: float,
                   knee: float):
    """Percentile black/white point with a soft shoulder.

    A hard stretch to 1.0 clipped 2.3% of the reference frame -- detail simply
    deleted from the brightest parts of a display. The tanh shoulder rolls
    highlights off instead of chopping them.
    """
    px = rgb[mask]
    lo = float(np.percentile(px, lo_pct))
    hi = float(np.percentile(px, hi_pct))
    if hi - lo < 1e-3:
        return rgb, (lo, hi)

    x = (rgb - lo) / (hi - lo)
    x = np.maximum(x, 0.0)
    over = x > knee
    x[over] = knee + (1.0 - knee) * np.tanh((x[over] - knee) / (1.0 - knee))
    return np.clip(x, 0.0, 1.0), (lo, hi)


def unsharp(img: Image.Image, radius: float, amount: float, threshold: int) -> Image.Image:
    """Threshold is the important knob: it keeps the mask off flat, noisy shadows."""
    if amount <= 0:
        return img
    return img.filter(
        ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 100), threshold=threshold)
    )


def parse_crop(spec: str, w: int, h: int) -> tuple[int, int, int, int]:
    m = re.fullmatch(r"center:(\d+(?:\.\d+)?)%", spec.strip())
    if m:
        frac = float(m.group(1)) / 100.0
        cw, ch = int(w * frac), int(h * frac)
        return ((w - cw) // 2, (h - ch) // 2, cw, ch)
    parts = spec.split(",")
    if len(parts) != 4:
        die(f"bad --crop {spec!r}; want X,Y,W,H or center:N%")
    x, y, cw, ch = (int(p) for p in parts)
    x, y = max(0, x), max(0, y)
    return (x, y, min(cw, w - x), min(ch, h - y))


# ------------------------------------------------------------------ super-resolution


def ensure_model(quiet: bool) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "4x-UltraSharp-fp16-opset17.onnx"
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    log(f"  [model]    downloading UltraSharp (~33MB) -> {path}", quiet=quiet)
    tmp = path.with_suffix(".part")
    try:
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        die(f"could not fetch model: {exc}")
    return path


def upscale(rgb: np.ndarray, factor: int, tile: int, quiet: bool) -> np.ndarray:
    """4x ESRGAN via onnxruntime, tiled with overlap.

    The ONNX export has dynamic height/width, so tiles can be any size. Each tile is
    run with a padded border which is then cropped off, which is what keeps the seams
    invisible.
    """
    import onnxruntime as ort  # lazy: keeps startup fast when --upscale is unused

    model = ensure_model(quiet)
    providers = [p for p in ("CoreMLExecutionProvider", "CPUExecutionProvider")
                 if p in ort.get_available_providers()]
    sess = ort.InferenceSession(str(model), providers=providers)
    name = sess.get_inputs()[0].name
    log(f"  [upscale]  {providers[0]}, tile {tile}", quiet=quiet)

    h, w, _ = rgb.shape
    pad = 16
    side = tile + 2 * pad  # every tile is this exact size -- see below
    out = np.zeros((h * MODEL_SCALE, w * MODEL_SCALE, 3), dtype=np.float32)

    for y0 in range(0, h, tile):
        for x0 in range(0, w, tile):
            y1, x1 = min(y0 + tile, h), min(x0 + tile, w)

            # Clamp the padded window to the image, then edge-replicate back up to a
            # constant `side`x`side`. Uniform shapes matter: CoreML recompiles the
            # graph for every new input shape, so ragged edge tiles cost more than
            # the pixels they carry.
            cy0, cx0 = max(0, y0 - pad), max(0, x0 - pad)
            cy1, cx1 = min(h, y1 + pad), min(w, x1 + pad)
            patch = rgb[cy0:cy1, cx0:cx1]

            top, left = pad - (y0 - cy0), pad - (x0 - cx0)
            patch = np.pad(
                patch,
                ((top, side - patch.shape[0] - top),
                 (left, side - patch.shape[1] - left),
                 (0, 0)),
                mode="edge",
            )

            inp = patch.transpose(2, 0, 1)[None].astype(np.float16)
            res = sess.run(None, {name: inp})[0][0].transpose(1, 2, 0).astype(np.float32)

            # The source pixel at (y0,x0) always sits exactly `pad` into the patch,
            # so the keep-region is the same offset for every tile.
            o = pad * MODEL_SCALE
            res = res[o:o + (y1 - y0) * MODEL_SCALE, o:o + (x1 - x0) * MODEL_SCALE]
            out[y0 * MODEL_SCALE:y1 * MODEL_SCALE,
                x0 * MODEL_SCALE:x1 * MODEL_SCALE] = res

    out = np.clip(out, 0.0, 1.0)
    if factor != MODEL_SCALE:
        # 4x then down beats a direct 2x model run: the network sees more context.
        img = Image.fromarray((out * 255).round().astype(np.uint8))
        img = img.resize((w * factor, h * factor), Image.LANCZOS)
        out = np.asarray(img, dtype=np.float32) / 255.0
    return out


# ------------------------------------------------------------------------------ cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quicksnap",
        description="Capture a clean webcam frame for a vision model to read.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  quicksnap -o scope.jpg --preset screen
  quicksnap --crop center:50% --upscale 2 --preset text
  quicksnap --device "Oleksii's iPhone Camera" --preset board
  quicksnap --list-devices
""",
    )
    p.add_argument("-o", "--out", default="snap.jpg", help="output path (default: snap.jpg)")
    p.add_argument("--device", default="HD Camera", help="avfoundation device name or index")
    p.add_argument("--list-devices", action="store_true", help="show cameras and exit")
    p.add_argument("--size", default="1920x1080", help="capture size (default: 1920x1080)")
    p.add_argument("--fps", type=int, default=30, help="requested framerate (default: 30)")
    p.add_argument("--pixel-format", default=None, help="force capture pixel format")

    p.add_argument("--warmup", default="auto",
                   help="frames to discard while 3A settles: N or 'auto' (default)")
    p.add_argument("--max-frames", type=int, default=12,
                   help="total frames to capture (default: 12)")

    p.add_argument("--preset", choices=sorted(PRESETS), default="general",
                   help="tuning profile (default: general)")
    p.add_argument("--wb", choices=["shades", "gray", "whitepatch", "none"], default="shades",
                   help="white balance estimator (default: shades)")
    p.add_argument("--no-levels", action="store_true", help="skip the levels stretch")
    p.add_argument("--usm", default=None, metavar="R:A:T",
                   help="override unsharp mask, e.g. 1.2:1.0:3")

    p.add_argument("--crop", default=None, metavar="SPEC",
                   help="X,Y,W,H or center:N%% -- the best 'zoom' for a vision model")
    p.add_argument("--upscale", type=int, choices=[2, 4], default=None,
                   help="super-resolve with UltraSharp (off by default)")
    p.add_argument("--tile", type=int, default=256, help="SR tile size (default: 256)")

    p.add_argument("--fit", type=int, default=CLAUDE_LONG_EDGE,
                   help=f"long edge cap (default: {CLAUDE_LONG_EDGE}, matches Claude)")
    p.add_argument("--full", action="store_true", help="no resize, keep native size")
    p.add_argument("--quality", type=int, default=92, help="JPEG quality (default: 92)")

    p.add_argument("--stats", action="store_true", help="report measurements")
    p.add_argument("--json", action="store_true", help="emit stats as JSON on stdout")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress progress")
    p.add_argument("--keep-raw", default=None, metavar="PATH",
                   help="also save the unprocessed chosen frame")
    return p


def main() -> int:
    args = build_parser().parse_args()
    quiet = args.quiet or args.json

    if args.list_devices:
        print(list_devices())
        return 0

    if not shutil.which("ffmpeg"):
        die("ffmpeg not found on PATH (brew install ffmpeg)")

    preset = dict(PRESETS[args.preset])
    if args.usm:
        try:
            r, a, t = args.usm.split(":")
            preset["usm"] = (float(r), float(a), int(t))
        except ValueError:
            die(f"bad --usm {args.usm!r}; want RADIUS:AMOUNT:THRESHOLD, e.g. 1.2:1.0:3")

    warmup: str | int = "auto"
    if args.warmup != "auto":
        try:
            warmup = int(args.warmup)
        except ValueError:
            die(f"bad --warmup {args.warmup!r}; want an integer or 'auto'")

    stats: dict = {"device": args.device, "preset": args.preset}

    with tempfile.TemporaryDirectory(prefix="quicksnap-") as td:
        tmp = Path(td)
        log(f"  [capture]  {args.size}, {args.max_frames} frames from {args.device!r}",
            quiet=quiet)
        frames = capture_frames(args.device, args.size, args.fps, args.max_frames,
                                tmp, args.pixel_format, quiet)
        chosen, scores, warm_used, idx = pick_frame(frames, warmup, quiet)

        stats["frame_scores"] = [round(s, 1) for s in scores]
        stats["warmup_frames"] = warm_used
        stats["chosen_frame"] = idx + 1
        stats["sharpness_raw"] = round(scores[idx], 1)

        img = Image.open(chosen).convert("RGB")
        if args.keep_raw:
            img.save(args.keep_raw, quality=95)

    if args.crop:
        x, y, cw, ch = parse_crop(args.crop, img.width, img.height)
        img = img.crop((x, y, x + cw, y + ch))
        stats["crop"] = [x, y, cw, ch]
        log(f"  [crop]     {cw}x{ch} at {x},{y}", quiet=quiet)

    rgb = np.asarray(img, dtype=np.float32) / 255.0
    mask = content_mask(rgb)

    bright = rgb[rgb.mean(axis=2) >= np.percentile(rgb.mean(axis=2), 80)].mean(axis=0)
    stats["bright_rgb_before"] = [round(float(v) * 255, 1) for v in bright]

    rgb, gains = white_balance(rgb, args.wb, mask)
    stats["wb_gains"] = [round(float(g), 3) for g in gains]
    log(f"  [wb]       gains R{gains[0]:.3f} G{gains[1]:.3f} B{gains[2]:.3f}", quiet=quiet)

    if not args.no_levels:
        rgb, (lo, hi) = stretch_levels(rgb, mask, preset["lo_pct"], preset["hi_pct"],
                                       preset["knee"])
        stats["levels"] = [round(lo, 4), round(hi, 4)]
        log(f"  [levels]   lo {lo:.4f}  hi {hi:.4f}  knee {preset['knee']}", quiet=quiet)

    if args.upscale:
        rgb = upscale(rgb, args.upscale, args.tile, quiet)

    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).round().astype(np.uint8))

    r, a, t = preset["usm"]
    img = unsharp(img, r, a, t)
    log(f"  [usm]      r{r} a{a} t{t}", quiet=quiet)

    if not args.full and args.fit and max(img.size) > args.fit:
        scale = args.fit / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)),
                         Image.LANCZOS)
        log(f"  [fit]      {img.width}x{img.height}", quiet=quiet)

    out = Path(args.out)
    if out.parent != Path(""):
        out.parent.mkdir(parents=True, exist_ok=True)
    # subsampling=0 keeps 4:4:4 chroma -- coloured text and thin traces survive it.
    save_kw = ({"quality": args.quality, "subsampling": 0}
               if out.suffix.lower() in (".jpg", ".jpeg") else {})
    img.save(out, **save_kw)

    final = np.asarray(img.convert("RGB"), dtype=np.float32)
    fg = final.mean(axis=2)
    stats["size"] = [img.width, img.height]
    stats["sharpness_final"] = round(sharpness(fg), 1)
    stats["clip_pct"] = round(float((fg >= 254).mean()) * 100, 3)
    fb = final[fg >= np.percentile(fg, 80)].mean(axis=0)
    stats["bright_rgb_after"] = [round(float(v), 1) for v in fb]
    stats["out"] = str(out)

    log(f"  -> {out}  ({img.width}x{img.height})", quiet=quiet)

    if args.json:
        print(json.dumps(stats, indent=2))
    elif args.stats:
        print(f"sharpness  {stats['sharpness_raw']} raw -> {stats['sharpness_final']} final")
        print(f"bright RGB {stats['bright_rgb_before']} -> {stats['bright_rgb_after']}")
        print(f"clipped    {stats['clip_pct']}%")
        print(f"frames     {stats['frame_scores']}  (chose #{stats['chosen_frame']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
