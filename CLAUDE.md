# quicksnap

Webcam capture tool that gives Claude eyes at the electronics bench. Single-file script by
design — `quicksnap.py` is the whole tool. Don't package it, don't split it into modules.

## Posture

- **A script stays a script.** PEP 723 inline deps, `uv run`, no `pyproject.toml`, no `src/`.
- Deps stay minimal: `numpy`, `pillow`, `onnxruntime`. **No torch** — the ONNX route was chosen
  specifically to avoid a 2.5GB dependency for one optional feature.
- The consumer of the output is a vision model, not a human eye. Legibility and colour fidelity
  win over aesthetic grade. Invented detail is worse than blur.

## Two sharpness metrics, on purpose

- `sharpness()` (variance of Laplacian) — used **only** to compare frames of one burst.
- `tenengrad()` (Sobel energy) — used for **focus** decisions.

They are not interchangeable. Variance-of-Laplacian responds to noise and to clipping as much
as to focus, and it has misled this project three separate times: it ranked a noisy high-gain
frame above the converged ones; it scored a ringing Richardson-Lucy output 4× above a more
readable original; and it called a defocused frame sharp because the subject had large
high-contrast features. Don't "simplify" by collapsing them into one.

For anything physical, prefer `edge_widths()` / `optical_report()` — those have real units
(pixels of 10–90% rise) and a known target (~1.0–1.5 px focused), so they can't flatter
themselves the way a unitless score can.

**`optical_report` takes the 10th percentile of edge widths, never the median.** A scene has
genuinely soft edges (shadows, gradients, out-of-focus background) as well as sharp ones, so
the median measures the *scene*, not the system. The PSF is a lower bound — nothing can be
sharper than it — so the narrowest edges are what reveal the optical limit. Shipped once with
the median and it reported ~10.9 px PSF and "161 resolvable" for a frame that visibly resolved
breadboard holes and readable on-screen text; p10 gave ~6.5 px on the same frame. If a
measurement disagrees with what the image obviously shows, distrust the measurement.

## Things that will break if you "clean them up"

- **`-fps_mode passthrough` in `capture_frames`.** Without it ffmpeg duplicates frames to pad
  the camera's real rate up to the requested one, and the burst becomes N copies of one bad
  frame.
- **One ffmpeg invocation for warm-up + burst.** Reopening the device resets 3A convergence.
- **`find_stable_start` does not pick the sharpest frame.** That's deliberate — see its
  docstring. An early noisy or blown-out frame can outscore the converged ones.
- **White balance is applied in linear light.** Applying gains to sRGB values skews hue, which
  matters when the subject is a resistor colour band.

## Measurements

README.md carries the numbers behind every default. If you change a default, re-measure and
update that table — the values are from one specific rig and are meant to be reproducible.

## Known gaps

- macOS only (avfoundation). Linux would need a v4l2 branch in `capture_frames`.
- No auto-detection of the screen rectangle; crops are manual.
- `--upscale` is slow (~80s) because CoreML partitions most of ESRGAN back to CPU.
