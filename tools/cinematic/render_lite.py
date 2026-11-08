#!/usr/bin/env python3
"""Render a simple EDL into a graded vertical reel with ffmpeg.

Usage: python3 render_lite.py catalog.json edl.json <clips_dir> out.mp4

EDL: {"output": {"width":1080,"height":1920,"fps":30},
      "look": {"saturation":1.1,"contrast":1.06,"warmth":0.06,"vignette":true},
      "title": {"text":"Spa by JW","sub":"Parq Vancouver","dur":2.5},
      "end_card": {"text":"JW Marriott Parq Vancouver","dur":2.0},
      "video": [{"shot_id":"...", "in":0.0, "out":1.0, "speed":1.0}, ...]}
in/out are relative to the shot start and optional (defaults to whole shot)."""
import json, subprocess, sys, tempfile
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT2 = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def main(cat_path, edl_path, clips_dir, out_path):
    cat = json.load(open(cat_path)); edl = json.load(open(edl_path))
    shots = {s["shot_id"]: s for s in cat["shots"]}
    assets = {a["asset_id"]: a for a in cat["assets"]}
    W, H, FPS = edl["output"]["width"], edl["output"]["height"], edl["output"]["fps"]
    look = edl.get("look", {})
    tmp = Path(tempfile.mkdtemp())
    segs = []
    for i, c in enumerate(edl["video"]):
        sh = shots[c["shot_id"]]; src = Path(clips_dir) / assets[sh["asset_id"]]["file"]
        start = sh["start"] + c.get("in", 0.0); end = sh["start"] + c.get("out", sh["duration"])
        speed = c.get("speed", 1.0)
        seg = tmp / f"seg{i:03d}.mp4"
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"setpts={1/speed:.4f}*PTS,fps={FPS},format=yuv420p")
        ff("-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-an", "-vf", vf,
           "-c:v", "libx264", "-preset", "fast", "-crf", "18", str(seg))
        segs.append(seg)
    # end card
    ec = edl.get("end_card"); 
    if ec:
        seg = tmp / "end.mp4"
        txt = ec["text"].replace(":", "\\:").replace("'", "\\'")
        ff("-f", "lavfi", "-i", f"color=c=0x0b0908:s={W}x{H}:r={FPS}:d={ec['dur']}",
           "-vf", f"drawtext=fontfile={FONT}:text='{txt}':fontcolor=0xe8dcc8:fontsize=56:x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p",
           "-c:v", "libx264", "-preset", "fast", "-crf", "18", str(seg))
        segs.append(seg)
    lst = tmp / "list.txt"; lst.write_text("".join(f"file '{s}'\n" for s in segs))
    # total duration for fade-out
    total = sum(float(subprocess.check_output(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                                                 "-of", "csv=p=0", str(s)])) for s in segs)
    sat, con, warm = look.get("saturation", 1.0), look.get("contrast", 1.0), look.get("warmth", 0.0)
    filters = [f"eq=saturation={sat}:contrast={con}",
               f"colorbalance=rs={warm:.3f}:bs={-warm:.3f}:rh={warm/2:.3f}:bh={-warm/2:.3f}",
               "curves=all='0/0.02 0.5/0.5 1/0.97'"]
    if look.get("vignette"):
        filters.append("vignette=PI/5")
    t = edl.get("title")
    if t:
        main_t = t["text"].replace(":", "\\:").replace("'", "\\'")
        filters.append(f"drawtext=fontfile={FONT}:text='{main_t}':fontcolor=white:fontsize=88:x=(w-text_w)/2:y=h*0.42"
                       f":enable='between(t,0.4,{t['dur']})':alpha='if(lt(t,0.9),(t-0.4)/0.5,if(gt(t,{t['dur']-0.5}),({t['dur']}-t)/0.5,1))'"
                       f":shadowcolor=black@0.5:shadowx=2:shadowy=2")
        if t.get("sub"):
            sub = t["sub"].replace(":", "\\:").replace("'", "\\'")
            filters.append(f"drawtext=fontfile={FONT2}:text='{sub}':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=h*0.42+110"
                           f":enable='between(t,0.7,{t['dur']})':alpha='if(lt(t,1.2),(t-0.7)/0.5,if(gt(t,{t['dur']-0.5}),({t['dur']}-t)/0.5,1))'")
    filters += [f"fade=t=in:st=0:d=0.6", f"fade=t=out:st={total-0.8:.2f}:d=0.8", "format=yuv420p"]
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-vf", ",".join(filters),
       "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-movflags", "+faststart", out_path)
    print(f"[render] {out_path} {total:.1f}s {len(segs)} segments")


if __name__ == "__main__":
    main(*sys.argv[1:5])
