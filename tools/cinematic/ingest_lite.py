#!/usr/bin/env python3
"""Phase 1 lite: turn a folder of clips into a browsable, labeled shot catalog.

Usage:
    python3 ingest_lite.py <input_dir> <output_dir> [--labels labels.json] [--min-shot 0.6]

--min-shot: shortest shot in seconds (0.4 for fast-cut edited reels, 1.0 for raw footage).

Per clip: ffprobe metadata, camera/log-profile guess, shot detection, 720p proxy,
per-shot thumbnails + contact sheet, quality scores (sharpness, clipping, motion).
Writes catalog.json, catalog.csv and a self-contained library.html.

Labels (subject, place, time of day, ...) are read from --labels if given, so the
vision-labeling step can be done by any model or by hand and merged in.
"""
import csv, json, math, os, re, subprocess, sys
from pathlib import Path

import cv2
import numpy as np
from scenedetect import AdaptiveDetector, SceneManager, open_video

VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mts", ".m4v", ".avi", ".mkv"}

CAMERA_RULES = [
    (re.compile(r"^DJI_\d{14}_\d{4}", re.I), "DJI", "check encoder tag / color_trc"),
    (re.compile(r"^C\d{4}\.(MP4|MOV)$", re.I), "Sony Alpha", "check color_trc"),
    (re.compile(r"^IMG_\d{4}\.(MOV|MP4)$", re.I), "iPhone", "Rec.709 or Apple Log"),
    (re.compile(r"^GX\d{6}\.MP4$", re.I), "GoPro", "flat or standard"),
]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def ffprobe(path):
    data = json.loads(run(["ffprobe", "-v", "quiet", "-print_format", "json",
                           "-show_format", "-show_streams", str(path)]))
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    tags = {**data["format"].get("tags", {}), **v.get("tags", {})}
    return {
        "duration": float(data["format"].get("duration", 0)),
        "width": v.get("width"), "height": v.get("height"), "fps": round(fps, 3),
        "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
        "color_space": v.get("color_space"), "color_transfer": v.get("color_transfer"),
        "color_primaries": v.get("color_primaries"),
        "bit_rate_mbps": round(int(data["format"].get("bit_rate", 0)) / 1e6, 1),
        "has_audio": a is not None,
        "creation_time": tags.get("creation_time"),
        "make": tags.get("com.apple.quicktime.make") or tags.get("make"),
        "model": tags.get("com.apple.quicktime.model") or tags.get("model"),
        "encoder": tags.get("encoder"),
    }


ENCODER_RULES = [
    (re.compile(r"OsmoPocket3", re.I), "DJI Osmo Pocket 3", "Normal (Rec.709) unless footage looks flat; D-Log M possible"),
    (re.compile(r"OsmoAction", re.I), "DJI Osmo Action", "Normal or D-Log M"),
    (re.compile(r"Mini|Air|Mavic", re.I), "DJI drone", "Normal or D-Log/D-Log M"),
]


def guess_camera(name, meta):
    enc = meta.get("encoder") or ""
    for rx, cam, log in ENCODER_RULES:
        if rx.search(enc):
            return cam, log
    for rx, cam, log in CAMERA_RULES:
        if rx.search(name):
            return cam, log
    if meta.get("make") or meta.get("model"):
        return f"{meta.get('make') or ''} {meta.get('model') or ''}".strip(), "check color_trc"
    return "unknown", "unknown"


def detect_shots(path, min_len_s=0.6):
    """AdaptiveDetector handles dissolves and camera flashes better than a fixed threshold.
    Shots shorter than min_len_s are merged into their neighbour."""
    video = open_video(str(path))
    sm = SceneManager()
    sm.add_detector(AdaptiveDetector(adaptive_threshold=2.5, min_scene_len=int(min_len_s * video.frame_rate)))
    sm.detect_scenes(video, show_progress=False)
    scenes = [(s.seconds, e.seconds) for s, e in sm.get_scene_list()]
    if not scenes:
        return [(0.0, video.duration.seconds)]
    merged = []
    for s, e in scenes:
        if merged and (e - s) < min_len_s:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def quality(path, start, end, samples=5):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    sharp, clip_hi, clip_lo, motion, prev = [], [], [], [], None
    for i in range(samples):
        t = start + (end - start) * (i + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        small = cv2.resize(frame, (480, int(480 * frame.shape[0] / frame.shape[1])))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        sharp.append(cv2.Laplacian(gray, cv2.CV_64F).var())
        clip_hi.append((gray >= 250).mean()); clip_lo.append((gray <= 5).mean())
        if prev is not None:
            motion.append(float(np.mean(cv2.absdiff(gray, prev))))
        prev = gray
    cap.release()
    s = float(np.median(sharp)) if sharp else 0.0
    score = min(1.0, math.log1p(s) / 7.0) * (1 - min(0.5, 3 * float(np.mean(clip_hi or [0])))) \
            * (1 - min(0.5, 2 * float(np.mean(clip_lo or [0]))))
    return {"sharpness": round(s, 1), "clip_highlights": round(float(np.mean(clip_hi or [0])), 4),
            "clip_shadows": round(float(np.mean(clip_lo or [0])), 4),
            "motion": round(float(np.mean(motion or [0])), 2), "quality_score": round(score, 2)}


def make_proxy(src, dst):
    if dst.exists():
        return
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=-2:720", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)])


def thumbs(proxy, start, end, out_prefix, n=4):
    paths = []
    for i in range(n):
        t = start + (end - start) * (i + 0.5) / n
        p = Path(f"{out_prefix}_{i}.jpg")
        if not p.exists():
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(proxy), "-frames:v", "1",
                 "-vf", "scale=320:-2", "-q:v", "4", str(p)])
        paths.append(p)
    return paths


def contact_sheet(thumb_paths, dst):
    imgs = [cv2.imread(str(p)) for p in thumb_paths if p.exists()]
    if not imgs:
        return
    h = min(i.shape[0] for i in imgs)
    imgs = [cv2.resize(i, (int(i.shape[1] * h / i.shape[0]), h)) for i in imgs]
    cv2.imwrite(str(dst), cv2.hconcat(imgs))


def build_html(catalog, out_dir):
    shots_json = json.dumps(catalog["shots"])
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clip Library</title><style>
body{{font-family:system-ui,sans-serif;margin:0;background:#111;color:#eee}}
header{{padding:12px 16px;background:#1b1b1b;position:sticky;top:0;display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
input,select{{background:#222;color:#eee;border:1px solid #444;padding:6px 8px;border-radius:6px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;padding:12px}}
.card{{background:#1b1b1b;border-radius:10px;overflow:hidden}}
.card img{{width:100%;display:block;aspect-ratio:16/9;object-fit:cover}}
.meta{{padding:8px 10px;font-size:12px;line-height:1.4}}
.tag{{display:inline-block;background:#2b3a55;color:#cfe0ff;border-radius:4px;padding:1px 6px;margin:1px 2px 0 0}}
.q{{float:right;font-weight:600}} video{{width:100%}}
.ok{{color:#8f8}} .mid{{color:#fd8}} .bad{{color:#f88}}
</style></head><body>
<header><strong>Clip Library</strong><span id="count"></span>
<input id="q" placeholder="search labels, camera, file…" size="30">
<select id="cam"><option value="">all cameras</option></select>
<select id="minq"><option value="0">any quality</option><option value="0.5">quality ≥ 0.5</option><option value="0.7">quality ≥ 0.7</option></select>
<label><input type="checkbox" id="hidedup"> hide duplicate takes</label></header>
<div class="grid" id="grid"></div>
<script>
const shots={shots_json};
const cam=document.getElementById('cam');[...new Set(shots.map(s=>s.camera))].forEach(c=>{{const o=document.createElement('option');o.value=o.textContent=c;cam.appendChild(o);}});
function render(){{const q=document.getElementById('q').value.toLowerCase(),c=cam.value,mq=+document.getElementById('minq').value,hd=document.getElementById('hidedup').checked;
const rows=shots.filter(s=>(!c||s.camera===c)&&s.quality_score>=mq&&(!hd||!s.duplicate_of)&&(!q||JSON.stringify(s).toLowerCase().includes(q)));
document.getElementById('count').textContent=rows.length+' / '+shots.length+' shots';
document.getElementById('grid').innerHTML=rows.map(s=>{{const qc=s.quality_score>=0.7?'ok':s.quality_score>=0.5?'mid':'bad';
const labels=Object.entries(s.labels||{{}}).flatMap(([k,v])=>Array.isArray(v)?v:[v]).filter(v=>v&&v!=='none'&&typeof v==='string').map(v=>`<span class="tag">${{v}}</span>`).join('');
return `<div class="card"><img src="${{s.contact_sheet}}" loading="lazy" onclick="if(s.proxy)play(this,s)">
<div class="meta"><span class="q ${{qc}}">${{s.quality_score}}</span><b>${{s.file}}</b> · shot ${{s.index}} · ${{s.start.toFixed(1)}}–${{s.end.toFixed(1)}}s<br>
${{s.camera}} · ${{s.log_profile}} · ${{s.width}}×${{s.height}} ${{s.fps}}fps${{s.duplicate_of?' · <i>dup of '+s.duplicate_of+'</i>':''}}<br>${{labels}}</div></div>`}}).join('');}}
function play(img,s){{const v=document.createElement('video');v.controls=true;v.autoplay=true;v.src=s.proxy+'#t='+s.start;img.replaceWith(v);}}
document.querySelectorAll('input,select').forEach(e=>e.addEventListener('input',render));render();
</script></body></html>"""
    (out_dir / "library.html").write_text(html)


def main():
    args = sys.argv[1:]
    labels_path = None
    if "--labels" in args:
        i = args.index("--labels"); labels_path = Path(args[i + 1]); del args[i:i + 2]
    min_shot = 0.6
    if "--min-shot" in args:
        i = args.index("--min-shot"); min_shot = float(args[i + 1]); del args[i:i + 2]
    in_dir, out_dir = Path(args[0]), Path(args[1])
    (out_dir / "proxies").mkdir(parents=True, exist_ok=True)
    (out_dir / "thumbs").mkdir(exist_ok=True)
    labels = json.loads(labels_path.read_text()) if labels_path and labels_path.exists() else {}

    assets, shots = [], []
    files = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in VIDEO_EXT)
    for f in files:
        print(f"[ingest] {f.name}", flush=True)
        meta = ffprobe(f)
        cam, log = guess_camera(f.name, meta)
        asset_id = re.sub(r"[^A-Za-z0-9]+", "_", f.stem)
        proxy = out_dir / "proxies" / f"{asset_id}.mp4"
        make_proxy(f, proxy)
        assets.append({"asset_id": asset_id, "file": f.name, "size_mb": round(f.stat().st_size / 1e6, 1),
                       "camera": cam, "log_profile": log, **meta})
        for idx, (s, e) in enumerate(detect_shots(f, min_shot)):
            shot_id = f"{asset_id}_s{idx:03d}"
            tp = thumbs(proxy, s, e, out_dir / "thumbs" / shot_id)
            sheet = out_dir / "thumbs" / f"{shot_id}_sheet.jpg"
            contact_sheet(tp, sheet)
            q = quality(proxy, s, e)
            shots.append({"shot_id": shot_id, "asset_id": asset_id, "file": f.name, "index": idx,
                          "start": round(s, 3), "end": round(e, 3), "duration": round(e - s, 3),
                          "camera": cam, "log_profile": log, "width": meta["width"], "height": meta["height"],
                          "fps": meta["fps"], "proxy": f"proxies/{proxy.name}",
                          "contact_sheet": f"thumbs/{sheet.name}", "thumbs": [f"thumbs/{p.name}" for p in tp],
                          **q, "labels": labels.get(shot_id, {}), "duplicate_of": None})

    # near-duplicate grouping: tiny grayscale thumbnails compared by mean abs diff
    sigs = {}
    for sh in shots:
        img = cv2.imread(str(out_dir / sh["thumbs"][len(sh["thumbs"]) // 2]), cv2.IMREAD_GRAYSCALE)
        sigs[sh["shot_id"]] = cv2.resize(img, (16, 9)).astype(np.float32) if img is not None else None
    for i, a in enumerate(shots):
        if a["duplicate_of"] or sigs[a["shot_id"]] is None:
            continue
        for b in shots[i + 1:]:
            if b["duplicate_of"] or sigs[b["shot_id"]] is None or b["asset_id"] != a["asset_id"] and b["camera"] != a["camera"]:
                continue
            if float(np.mean(np.abs(sigs[a["shot_id"]] - sigs[b["shot_id"]]))) < 12:
                b["duplicate_of"] = a["shot_id"]

    catalog = {"assets": assets, "shots": shots}
    (out_dir / "catalog.json").write_text(json.dumps(catalog, indent=2))
    with open(out_dir / "catalog.csv", "w", newline="") as fh:
        cols = ["shot_id", "file", "index", "start", "end", "duration", "camera", "log_profile", "width", "height",
                "fps", "sharpness", "clip_highlights", "clip_shadows", "motion", "quality_score", "duplicate_of", "labels"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for sh in shots:
            w.writerow({**sh, "labels": json.dumps(sh["labels"])})
    build_html(catalog, out_dir)
    print(f"[done] {len(assets)} assets, {len(shots)} shots -> {out_dir/'library.html'}")


if __name__ == "__main__":
    main()
