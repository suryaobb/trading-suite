# Cinematic ingest (Phase 1 lite)

Turns a folder of clips into a labeled, browsable shot catalog. Proof of concept for the
Cinematic Editor plan in `docs/cinematic-video-platform/PLAN.md`.

```bash
pip install scenedetect opencv-python-headless   # ffmpeg/ffprobe must be on PATH
python3 ingest_lite.py <clips_dir> <out_dir> [--min-shot 0.6] [--labels labels.json]
open <out_dir>/library.html
```

- `--min-shot`: shortest shot in seconds. Use 0.4 for fast-cut edited reels, 1.0 for raw footage.
- `--labels`: JSON `{shot_id: {...}}` merged into the catalog. Produced by a vision model
  (or by hand) from the contact sheets in `<out_dir>/thumbs/*_sheet.jpg`.

Outputs: `catalog.json`, `catalog.csv`, `proxies/` (720p), `thumbs/`, `library.html`.

`sample_output/` holds the first real test: 3 clips from the owner's Drive
(a DJI Osmo Pocket 3 clip and two JW Marriott Parq Vancouver spa reels) → 38 labeled shots.
`library_standalone.html` has thumbnails inlined so it opens anywhere; playback needs the proxies.
