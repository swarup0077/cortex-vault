# Screenshot vs photo on the real files (item w)

CLIP rule alone (SPEC §6.2 rule 4) at `clip_screenshot_margin` = 0.04, on each image's stored CLIP vector. Labels from names and EXIF: "Screenshot…" is a screenshot; "IMG_…" or a camera in EXIF is a photo. Measurement only: no value changed.

| set | label | n | CLIP right | lead (screenshot − photo cosine): min / median / max |
|---|---|---|---|---|
| all | screenshot | 12 | 0 (0%) | 0.004 / 0.017 / 0.035 |
| all | photo | 0 | – | – |
| all | both | 12 | 0 (0%) | 0.004 / 0.017 / 0.035 |
| public | screenshot | 3 | 0 (0%) | 0.011 / 0.020 / 0.022 |
| public | photo | 0 | – | – |
| public | both | 3 | 0 (0%) | 0.011 / 0.020 / 0.022 |
| private | screenshot | 9 | 0 (0%) | 0.004 / 0.015 / 0.035 |
| private | photo | 0 | – | – |
| private | both | 9 | 0 (0%) | 0.004 / 0.015 / 0.035 |

In the app, the labelled images were decided by filename (rules 1-2), so their kind is right there; the unlabelled ones by clip: 5 screenshots, 13 photos.

Images without a label (no "Screenshot" or "IMG_" name, no camera EXIF; forwarded chat images): 18 (14 public, 4 private); CLIP calls 5 of them screenshots. No label, so no accuracy.

Labelled images CLIP gets wrong (12):

| file | set | label | CLIP | lead |
|---|---|---|---|---|
| corpus/real/Screenshot 2025-11-17 193345.png | public | screenshot | photo | 0.020 |
| corpus/real/Screenshot 2025-11-17 192036.png | public | screenshot | photo | 0.011 |
| corpus/real/Screenshot 2025-11-17 191854.png | public | screenshot | photo | 0.022 |
| private-19 | private | screenshot | photo | 0.008 |
| private-18 | private | screenshot | photo | 0.004 |
| private-17 | private | screenshot | photo | 0.004 |
| private-16 | private | screenshot | photo | 0.019 |
| private-15 | private | screenshot | photo | 0.034 |
| private-14 | private | screenshot | photo | 0.026 |
| private-13 | private | screenshot | photo | 0.015 |
| private-12 | private | screenshot | photo | 0.035 |
| private-11 | private | screenshot | photo | 0.005 |
