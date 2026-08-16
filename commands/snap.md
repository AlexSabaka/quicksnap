---
description: "Take a webcam snapshot of the bench and look at it"
allowed-tools: ["Bash(uv run quicksnap.py:*)", "Read"]
---

# /snap

Capture a frame from the bench camera and describe what you see.

Arguments (all optional): `$ARGUMENTS` — may name a preset (`screen`, `board`, `text`,
`general`), a crop (`center:40%` or `X,Y,W,H`), a device name, or what to look for.

Steps:

1. Run the capture, translating any arguments into flags. Default to `--preset general` and
   write to `/tmp/quicksnap-latest.jpg`:

   ```bash
   uv run quicksnap.py -o /tmp/quicksnap-latest.jpg --preset general --stats
   ```

2. `Read` the resulting file.

3. Describe what is visible, answering whatever the user asked about. If they asked for a
   specific reading (a number on a display, which LED is lit, a part marking), state it
   plainly — and say so explicitly if it is not legible rather than guessing.

4. If `sharpness_raw` came back very low, or `clip_pct` is above ~1%, say the shot was poor
   and offer to re-take it cropped to the region of interest.

Do not use `--upscale` unless the user asks for it; cropping tighter is the better way to
resolve small detail.
