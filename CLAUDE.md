# quicksnap

Webcam capture tool that gives Claude eyes at the electronics bench. Single-file script by
design — `quicksnap.py` is the whole tool. Don't package it, don't split it into modules.

## Posture

- **A script stays a script.** PEP 723 inline deps, `uv run`, no `pyproject.toml`, no `src/`.
- Deps stay minimal: `numpy`, `pillow`, `onnxruntime`. **No torch** — the ONNX route was chosen
  specifically to avoid a 2.5GB dependency for one optional feature.
- The consumer of the output is a vision model, not a human eye. Legibility and colour fidelity
  win over aesthetic grade. Invented detail is worse than blur.

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
