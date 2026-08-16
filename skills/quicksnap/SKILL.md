---
name: quicksnap
description: Look at the physical bench through a webcam. Use when the user asks you to look at / check / read something physical — a scope trace, a multimeter or LCD or 7-segment readout, which LEDs are lit, breadboard or PCB wiring, a solder joint, a part number or silkscreen — or says "look at this", "what does the display say", "can you see the board", "take a photo/snapshot". Also use when debugging hardware and a screenshot or UART capture cannot show the answer, or the user references a camera, webcam, or `quicksnap`.
allowed-tools: Bash, Read
---

# quicksnap — bench eyes

Captures a clean frame from a webcam and hands you a file to `Read`. The point is to see
physical things that no screenshot or serial log can show.

## Use it

```bash
uv run quicksnap.py -o /tmp/snap.jpg --preset screen --stats
```

Then `Read /tmp/snap.jpg`. Always `Read` the file — the CLI only reports measurements, it
cannot tell you what is in the picture.

Pick a preset by subject:

| preset | for |
|---|---|
| `screen` | scope traces, LCDs, 7-segment, monitors — gentle sharpening so the pixel grid doesn't turn to moiré |
| `board` | PCBs, breadboards, wiring — keeps colour honest, which is what resistor bands and LED states depend on |
| `text` | silkscreen, part numbers, datasheet pages — pushes local contrast hard |
| `general` | default, anything else |

## Cropping is the zoom — use it

Claude downscales images to ~1568px on the long edge, so a full 1080p frame of a whole bench
gives roughly 1568px spread across everything. To read a small display, crop to it:

```bash
uv run quicksnap.py -o /tmp/scope.jpg --crop 490,25,970,820 --preset screen
uv run quicksnap.py -o /tmp/mid.jpg   --crop center:40% --preset text
```

Workflow that works: take one wide shot, `Read` it to find where the thing is, then re-shoot
cropped to that region. A tight crop beats any amount of upscaling.

## Judging your own shot

`--stats` prints what happened. Re-shoot if:

- **`sharpness_raw` is very low** (order ~15 rather than ~120 on a typical rig) — 3A never
  converged. Raise `--max-frames 20`.
- **`clip_pct` above ~1%** — highlights blown, detail gone from bright areas. Try
  `--preset screen`, which rolls highlights off earlier.
- **`frame_scores` are all near-identical AND low** — the camera may be capped or occluded.

`frame_scores` shows every captured frame; the first one or two being ~10× worse than the rest
is normal and expected — that is exactly what the tool exists to discard.

## Super-resolution: usually don't

`--upscale 2` / `--upscale 4` runs UltraSharp (4× ESRGAN). Off by default, and it should
usually stay off:

- It costs **~80s versus ~4s**, roughly 20× the wall-clock.
- It trades real texture for invented texture. Measured on a display photo, flat surfaces came
  back looking painterly and *lost* fine structure while looking crisper.
- It is a generative model. It reconstructs plausible detail, which is not the same as true
  detail. **Never rely on it to resolve a digit, a part number, or a colour band you could not
  already read** — if a reading matters, crop tighter and re-shoot instead.

It is reasonable for texture and general scenes where nothing is being *read*. If you do use
it, crop first: 4× on a full frame produces pixels that get thrown away in downscaling.

## Notes

- First run downloads ~40MB of Python deps via `uv`; `--upscale` additionally fetches a 33MB
  model to `~/.cache/quicksnap/`. Both cache.
- `--list-devices` shows cameras. `--device "..."` selects one.
- Capture takes ~4s. Don't call it in a tight loop.
