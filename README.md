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

## Focus first

If your shots are soft, **measure before you reach for any filter**:

```bash
uv run quicksnap.py --device 0 --measure --panel-px 320
```

```
blur              sharpest edges 6.53 px 10-90% (of 1639 edges; scene median 12.75)
                  PSF FWHM ~6.0 px   (good optics: ~1.0-1.5)
resolvable across 294 elements
target panel      320 px -> NOT RESOLVED
```

`resolvable across` is the honest capability number: how many distinct elements the system can
lay across the frame. A FWHM far above ~1.5 px means detail above the optical cutoff is
destroyed, not attenuated — no amount of sharpening, deconvolution or super-resolution brings
it back. Measured on this rig, the target LCD's own pixel lattice sat 25 dB down, i.e. absent.

**Why the sharpest decile, not the median.** A scene contains genuinely soft edges — shadows,
gradients, out-of-focus background — as well as sharp ones, so the median measures the *scene*.
The PSF is a lower bound: nothing can be sharper than it, so the narrowest edges reveal the
system limit. This tool originally used the median and was badly wrong, reporting ~10.9 px and
"161 resolvable" for a frame that visibly resolved breadboard holes and legible on-screen text.

**Resolution still buys real detail** — verify it on your own rig rather than assuming:

| `--size` | PSF (sharpest decile) | resolvable across |
|---|---|---|
| 640×480 | 3.87 px | 165 |
| 1280×720 | 5.24 px | 244 |
| 1920×1080 | 6.83 px | **281** |

Resolvable detail *rises* with capture size, so 1080p is not interpolated from a smaller
sensor — but it is nearing an optical ceiling around ~280–300. Keep raising `--size` until this
number stops climbing.

If the number is poor at *every* subject distance, it's the lens rather than focus. These
modules use M12/S-mount lenses, which have no focus ring — **the thread is the focus
mechanism**, though it is often glued at the factory. Turn the barrel while watching:

```bash
uv run quicksnap.py --device 0 --focus-assist --crop center:40%
```

```
     9052.0   99.5% of best |#############################################|  <== PEAK
```

Stop at the peak, lock the barrel, then re-run `--measure` to confirm. If the barrel won't
turn it is glued — that is common, and freeing it means heat or solvent, not a five-minute job.

Failing that, the lever that remains is **framing**: `resolvable across` is spread over the
whole frame, so whatever you need to read should fill as much of it as possible. A 320-px panel
occupying half the frame gets ~150 elements; filling the frame it gets ~290. Move the camera or
re-aim it before reaching for any software stage.

## Deconvolution (`--deconv`)

Off by default; genuinely useful when you need to read small text.

```bash
uv run quicksnap.py --device 0 --crop 490,290,440,110 --preset screen --deconv
```

It measures the PSF **from the frame itself** — averaging ~1000+ edge profiles, each aligned to
its own sub-pixel 50% crossing (the ISO 12233 slanted-edge idea, applied to whatever edges the
scene happens to contain) — then runs Richardson-Lucy with total-variation regularization.
Measurement happens on the full frame before cropping, since the PSF belongs to the optics and
a tight crop rarely holds enough edges.

**Measuring beats assuming.** On the reference rig the profile fits a Moffat (a 4.1, β 2.5)
far better than a defocus disc — a lens-aberration signature, not a focus error, which is why
nothing was sharp at any subject distance. An earlier attempt here used a *guessed* Gaussian /
disc kernel with *unregularized* RL and concluded deconvolution was useless. That conclusion
was wrong; it was testing a bad kernel and a known-ringy algorithm.

What the regularization buys, measured:

| variant | tenengrad | pixels overshooting |
|---|---|---|
| original | 5074 | 0% |
| Wiener (nsr 0.003) | 49655 | 26.7% |
| RL, no TV | 23275 | 12.4% |
| **RL + TV 0.02** (default) | 14730 | **6.7%** |
| RL + TV 0.05 | 12081 | 5.5% |

**It recovers rather than invents** — the important check. Deconvolving four *independent*
frames of the same scene produced identical readings, and the inter-frame noise/contrast ratio
was **0.98×** the original's. A process hallucinating detail from noise would scatter between
frames and drive that ratio up.

### Where it fails

**Clipped highlights get worse, not better.** RL cannot recover detail the sensor never
recorded, and its multiplicative update pushes saturated regions further. On the test frame the
bright yellow glyphs sharpened clearly while dark text inside a blown-white box became *less*
readable. Check `clipped_pct` from `--measure` first — if the thing you need to read sits in a
blown region, fix exposure or framing instead.

`--deconv-tv` (default 0.02) and `--deconv-iters` (default 40) are there to tune. Setting
`--deconv-tv 0` gives plain RL, which rings. When `--deconv` is on, the preset's unsharp mask
is automatically cut to 35% so the two don't sharpen the same edges twice.

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

## Licence

MIT — see [LICENSE](LICENSE). That covers this repo's code. UltraSharp is
CC-BY-NC-SA-4.0 and is **downloaded at runtime rather than vendored**, so it doesn't
change the licence of anything here; but if you enable `--upscale`, that model's
non-commercial terms apply to how you use its output.

## Requirements

- `ffmpeg` (`brew install ffmpeg`) — macOS/avfoundation for now.
- `uv`. Dependencies are declared inline (PEP 723) and resolve on Python 3.14:
  `numpy`, `pillow`, `onnxruntime`. No torch.

## As a Claude Code plugin

Ships a `quicksnap` skill (so Claude reaches for the camera when you ask it to look at
something) and a `/snap` command.

## Useful flags

```
--device N          camera INDEX (--list-devices to enumerate)
--rotate 0|90|180|270   for awkwardly mounted cameras
--focus-assist      live sharpness readout; turn the barrel until it peaks
--measure           physical blur report (edge rise, PSF FWHM, exposure)
--panel-px N        with --measure: is an N-px-wide display actually resolved?
--size WxH          capture size (default 1920x1080)
--warmup auto|N     frames to discard while 3A settles
--max-frames N      capture budget (default 12)
--crop SPEC         X,Y,W,H or center:N%
--preset NAME       general | screen | board | text
--usm R:A:T         override unsharp mask
--wb MODE           shades | gray | whitepatch | none
--no-levels         skip the levels stretch
--deconv            measure the PSF and deconvolve (recovers real detail)
--deconv-tv L       TV regularization strength (default 0.02)
--deconv-iters N    Richardson-Lucy iterations (default 40)
--upscale 2|4       super-resolve (slow; generative — see above)
--fit N / --full    long-edge cap, or native size
--stats / --json    report measurements
--keep-raw PATH     also save the unprocessed frame
```
