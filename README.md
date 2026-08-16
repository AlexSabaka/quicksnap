# quicksnap

Grab a webcam frame that's actually worth looking at — so Claude can see the bench during
electronics work. Scope traces, LCD and 7-segment readouts, which LEDs are lit, breadboard
wiring, solder joints: the things a screenshot or a UART dump can't show.

```bash
uv run quicksnap.py -o snap.jpg --preset screen --stats
```

~4 seconds, one file, no venv to manage.

## The problem it solves

The obvious command:

```bash
ffmpeg -y -f avfoundation -framerate 30 -video_size 1280x720 -i "HD Camera" -frames:v 1 out.jpg
```

grabs frame **#1** — before the camera's autofocus, auto-exposure and auto-white-balance have
converged. On the reference rig that frame scores **15.0** on variance-of-Laplacian. Frame #4
scores **109.5**. Everything else this tool does is a rounding error next to throwing away the
first few frames.

## Measurements

All on one machine (M-series Mac, a generic "HD Camera" USB webcam), sharpness =
variance of the Laplacian, JPEG quality pinned at `-q:v 2` so compression doesn't confound it.

| what | result |
|---|---|
| Frame 1 vs converged frames | **15.0 → ~109** (~7×), converging by frame 4 (~0.7s) |
| Full pipeline, naive capture → processed | **16.7 → 168.9** (~10×), no ML involved |
| Native capture size | camera does **1920×1080**; the usual 1280×720 throws away half the detail for free |
| Temporal averaging (`tmix`) | **worse** — 35.9 vs 46.4. Ghosts anything moving. Selection, never averaging |
| Lucky-frame pick, once converged | ~1.02× — cheap insurance against a torn frame, not a headline feature |
| White balance | bright-region RGB 185/220/226 → ~199/218/221; gains R 1.16–1.21, G 1.02–1.03, B 1.00 |
| Super-resolution | **~80s vs ~4s** for a modest crop, and trades real texture for invented texture |

Two traps worth knowing if you touch the capture code:

- **`-fps_mode passthrough` is mandatory.** Without it ffmpeg pads the camera's real ~5fps up
  to the requested framerate by *duplicating* frames — a 60-frame burst becomes 60 identical
  copies of one pre-convergence frame. (Observed; every frame byte-identical.)
- **One ffmpeg invocation.** Closing and reopening the device resets 3A convergence, so warm-up
  and the burst must share a single open handle.

## Choosing a frame

`--warmup auto` (the default) does *not* pick the sharpest frame. 3A converges *towards* a
steady state, so the steady state is the tail, and an early frame can outscore it for the wrong
reasons — a high-gain frame is noisy, and a mid-hunt frame can be blown out and high-contrast.
Both inflate variance-of-Laplacian.

A real burst that broke the naive rule:

```
[20.0, 245.6, 176.9, 176.1, 177.0, 132.6, 133.1, 134.3, 123.7, 125.6, 124.1, 126.2]
       ^^^^^ sharpest, and overexposed + grainy
```

So `auto` anchors on the last few frames and walks backwards while exposure *and* detail still
match that steady state, then takes the sharpest survivor. Pass `--warmup N` to override.

## Cropping is the zoom

Claude downscales images to ~1568px on the long edge. A full 1080p frame of the whole bench
spends those pixels on everything at once. To read a small display, crop to it:

```bash
uv run quicksnap.py -o scope.jpg --crop 490,25,970,820 --preset screen
uv run quicksnap.py -o mid.jpg   --crop center:40% --preset text
```

A tight crop beats any amount of upscaling — and it's ~20× faster.

## Presets

| preset | tuned for |
|---|---|
| `general` | default |
| `screen` | scope traces, LCDs, 7-segment, monitors — gentle sharpening (moiré) and early highlight rolloff |
| `board` | PCBs, breadboards — conservative levels, because resistor bands and LED states are colour |
| `text` | silkscreen, part numbers, datasheet pages — strong local contrast |

Override the sharpening directly with `--usm RADIUS:AMOUNT:THRESHOLD`, e.g. `--usm 1.2:1.0:3`.
The threshold is the knob that stops it amplifying noise in flat shadows.

## Pipeline

1. **Capture** — one `ffmpeg` avfoundation call, native 1920×1080, `-fps_mode passthrough`.
2. **Frame choice** — score every frame, discard the ones still converging, keep the sharpest.
3. **White balance** — shades-of-grey (Minkowski p=6) over a not-black-not-blown mask, applied
   in linear light. Gains applied straight to sRGB skew hue, which matters when the subject is
   a resistor colour band. `--wb shades|gray|whitepatch|none`.
4. **Levels** — percentile black/white point with a `tanh` shoulder. A hard stretch clipped
   2.3% of the reference frame; the shoulder rolls highlights off instead of deleting them.
5. **Unsharp mask** — radius / amount / threshold.
6. **Super-resolution** — optional, off by default. See below.
7. **Fit** — long edge capped at 1568 to match what Claude ingests. `--full` keeps native size.

## Super-resolution (optional, usually skip)

`--upscale 2` or `--upscale 4` runs [Kim2091/UltraSharp](https://huggingface.co/Kim2091/UltraSharp),
a 4× ESRGAN, via `onnxruntime` (fp16 ONNX, CoreML with CPU fallback, overlap-tiled). The model
is fetched to `~/.cache/quicksnap/` on first use (33MB). `--upscale 2` runs 4× then
Lanczos-downsamples, which beats a direct 2× because the network sees more context.

It is off by default on purpose:

- **~80s vs ~4s.** CoreML partitions most of the ESRGAN graph back to CPU.
- **It invents texture.** On a display photo, flat surfaces came back painterly — crisper
  looking while carrying *less* true structure.
- **It is generative.** Reconstructed detail is plausible, not real. Don't use it to resolve a
  digit, part number, or colour band you couldn't already read; crop tighter and re-shoot.

> **Licence:** UltraSharp is CC-BY-NC-SA-4.0 — **non-commercial**. Relevant if this ever points
> at a client's hardware. The rest of quicksnap has no such restriction; the default path never
> touches the model.

## Requirements

- `ffmpeg` (`brew install ffmpeg`) — macOS/avfoundation for now.
- `uv`. Dependencies are declared inline (PEP 723) and resolve on Python 3.14:
  `numpy`, `pillow`, `onnxruntime`. No torch.

## As a Claude Code plugin

Ships a `quicksnap` skill (so Claude reaches for the camera when you ask it to look at
something) and a `/snap` command.

## Useful flags

```
--device NAME       camera (--list-devices to enumerate)
--size WxH          capture size (default 1920x1080)
--warmup auto|N     frames to discard while 3A settles
--max-frames N      capture budget (default 12)
--crop SPEC         X,Y,W,H or center:N%
--preset NAME       general | screen | board | text
--usm R:A:T         override unsharp mask
--wb MODE           shades | gray | whitepatch | none
--no-levels         skip the levels stretch
--upscale 2|4       super-resolve (slow; see above)
--fit N / --full    long-edge cap, or native size
--stats / --json    report measurements
--keep-raw PATH     also save the unprocessed frame
```
