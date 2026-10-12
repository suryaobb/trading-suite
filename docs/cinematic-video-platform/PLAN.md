# Cinematic Video Platform — Product & Technical Plan

Status: **draft for review** (v0.1, 2026-10-12)

A prompt-driven editing system that turns a labeled library of cinematic clips into
finished, color-graded videos. Two product lines share one engine:

| Line | Example | Length | Audio | Feel |
|------|---------|--------|-------|------|
| **Postcard** | Hotel reel, destination teaser, travel montage | 15 s – 90 s | Music-driven, no dialogue | Slow, graded, beat-cut, logo end card |
| **Vlog** | Cabin build week 3, making a silver ring | 4 – 20 min | Talking + b-roll + music bed | Narrative, transcript-driven, chaptered |

Desktop is the primary workstation. The phone is for browsing the library, sending
prompts, reviewing drafts, and approving renders.

---

## 1. Goals and non-goals

**Goals**

1. Connect a database of clips (local drive, NAS, or cloud bucket) and have every clip
   automatically proxied, labeled, transcribed, and searchable.
2. Type a prompt ("30 s golden-hour pool reel for Hotel X, warm look, slow") and get a
   draft edit with music, grade, titles, and an end card.
3. Apply a consistent, repeatable color grade per client or per channel, including
   correct handling of log footage.
4. Edit long-form vlogs from the transcript: pull selects, cut silences, drop in matching
   b-roll, add chapters and captions.
5. Review and approve from a phone; render on the desktop or a worker.
6. Hand off to DaVinci Resolve when a project needs a human finishing pass.

**Non-goals (for v1)**

- A full manual NLE with frame-accurate keyframing. We generate edits; fine surgery
  happens in Resolve via export.
- Generating footage with AI. Real footage is the product. (Optional later: AI upscaling
  and sky/plate fixes.)
- Multi-tenant SaaS from day one. Build for one operator with multiple client
  workspaces; keep the data model ready for tenants.

---

## 2. User workflows

### 2.1 Ingest and organize ("sort through")

1. Point the app at a source: a folder, an external drive, or an S3/R2 bucket.
2. The ingest worker, per file:
   - reads metadata with ffprobe (codec, resolution, fps, duration, camera, color
     space/transfer, GPS/date if present);
   - detects scene/shot boundaries so one long recording becomes many "shots";
   - renders a 720p H.264 proxy (for preview and AI) and an HLS version for phone;
   - extracts thumbnails and a contact sheet per shot;
   - transcribes speech (Whisper) with word timestamps, flags silence;
   - labels each shot with a vision model: subject, location type, time of day,
     weather, camera move (static / pan / gimbal / drone / handheld), shot size
     (wide / medium / close / detail), mood, people present, text on screen;
   - scores technical quality: sharpness, exposure clipping, stability, noise;
   - computes a visual embedding per shot for semantic search ("misty lake at dawn");
   - detects near-duplicates (ten takes of the same pan) and groups them.
3. Library view: filter by project, client, label, date, quality score; search by text;
   "find similar" from any shot; batch-tag; mark favorites and rejects.

### 2.2 Define a Look (grade)

A **Look** is a named, versioned recipe:

- input transform per camera (e.g. S-Log3 → Rec.709, D-Log → Rec.709, Apple Log → Rec.709);
- creative 3D LUT (.cube) and strength;
- lift/gamma/gain, saturation, contrast, highlight roll-off, grain, vignette;
- optional reference frame for automatic shot matching (match exposure and white
  balance across clips before the creative LUT);
- typography, palette, logo, lower-third style, end-card template;
- pacing defaults (average shot length, transition type), preferred aspect ratios,
  music mood tags.

Looks belong to a **Workspace** (e.g. "Hotel Aurelia", "Cabin Vlog", "Jewelry").

### 2.3 Prompt → edit (Postcard mode)

1. Choose workspace, pick or auto-select music, write the prompt.
2. The edit agent (an LLM with tools against the catalog) produces a plan: target
   duration, aspect, section structure (hook → establishing → details → people/food →
   signature moment → end card), then searches the catalog section by section with
   constraints (quality ≥ threshold, no near-duplicates, variety of shot sizes, time of
   day continuity).
3. Beat detection on the music gives cut points; shots are trimmed to the best interval
   (highest motion/quality moment) and aligned to beats.
4. Output is an **EDL** (edit decision list, JSON) — not a video yet. The EDL is the
   contract between brain and renderer.
5. Fast proxy render (seconds) for review → tweak via chat ("swap the third shot for a
   drone shot", "slower", "more people") → final render at full resolution.

### 2.4 Prompt → edit (Vlog mode)

1. Prompt describes the story: "Week 3 of the cabin, focus on the roof framing, keep the
   part where I explain the rafter math, cut anything over 12 minutes."
2. The agent works from the transcript: scores each spoken segment for relevance and
   energy, removes silences/filler, builds a spoken spine, then inserts b-roll whose
   labels/embeddings match what's being said at that moment.
3. Adds chapters, burned or sidecar captions, music bed with ducking under speech,
   intro/outro from the Look.
4. Same review loop; export OTIO/FCPXML to Resolve for a manual pass when wanted.

### 2.5 Review and approve (phone)

- Watch the HLS proxy of a draft, scrub, leave timestamped comments ("too dark",
  "replace this").
- Comments are fed back to the agent as revision prompts.
- Approve → final render queued → notification with download/share link.

---

## 3. System architecture

```
                 ┌──────────────────────────────────────────────┐
                 │               Web app (Next.js)              │
  desktop ──────▶│  Library · Looks · Prompt chat · Timeline    │
  phone (PWA) ──▶│  Review/approve · Comments · Notifications   │
                 └──────────────┬───────────────────────────────┘
                                │ HTTPS / WebSocket
                 ┌──────────────▼───────────────────────────────┐
                 │               API (FastAPI, Python)          │
                 │  auth · workspaces · catalog · EDL · jobs    │
                 └───┬──────────────┬───────────────┬──────────┘
                     │              │               │
           ┌─────────▼───┐  ┌───────▼───────┐  ┌────▼─────────────┐
           │ Postgres +  │  │ Object store  │  │ Job queue (Redis)│
           │ pgvector    │  │ originals,    │  └────┬─────────────┘
           │ catalog/EDL │  │ proxies, HLS, │       │
           └─────────────┘  │ renders, LUTs │  ┌────▼─────────────────────────┐
                            └───────────────┘  │ Workers (Python, GPU optional)│
                                               │  ingest · label · transcribe │
                                               │  embed · beat-detect · render │
                                               └────┬────────────┬────────────┘
                                                    │            │
                                          ┌─────────▼──┐   ┌─────▼──────────────┐
                                          │ ffmpeg +   │   │ Claude (edit agent,│
                                          │ LUT/OTIO   │   │ vision labeling)   │
                                          └────────────┘   └────────────────────┘
```

**Deployment shape for v1:** everything runs on the desktop machine (Docker Compose),
footage on a local/NAS drive, and the web app is reachable from the phone over
Tailscale (or a small cloud VM fronts the API and serves proxies from R2 if you want
access away from home). Cloud GPU is optional and only needed if the desktop lacks one.

### 3.1 Component choices

| Concern | Choice | Why |
|---------|--------|-----|
| Backend | Python 3.12, FastAPI | Best ecosystem for ffmpeg, Whisper, PySceneDetect, librosa, OpenTimelineIO; matches your existing Python tooling |
| Frontend | Next.js + React + Tailwind, installable PWA | One codebase for desktop and phone; HLS playback works everywhere |
| Catalog DB | Postgres + pgvector | Relational catalog plus vector search in one place |
| Object store | Local disk / NAS for v1; S3-compatible (Cloudflare R2) when remote access is needed | Cheap, no egress fees, works with every tool |
| Queue | Redis + RQ (or Celery) | Simple, Python-native, visible job state |
| Shot detection | PySceneDetect | Robust content-aware cuts |
| Transcription | faster-whisper (local GPU/CPU) | Word timestamps, offline, free |
| Visual labels | Claude vision on sampled frames (Haiku-class for bulk, Sonnet/Opus-class for hard shots); optional Gemini video input for long clips | Structured JSON labels, cheap at scale |
| Embeddings | SigLIP/CLIP (open-source, local) | Fast "find similar" and text-to-shot search without API cost |
| Quality scoring | OpenCV (Laplacian sharpness, histogram clipping, optical-flow jitter) | Deterministic, free |
| Beat detection | librosa (onset/tempo) | Good enough for cut alignment |
| Edit agent | Claude (Fable/Opus-class) with tool use over the catalog | Reasoning + constrained tool calls; never invents clip IDs |
| Render | ffmpeg filtergraph: `lut3d`, `eq`, `curves`, `zoompan`, `xfade`, `drawtext`/ASS subtitles, `sidechaincompress` for ducking | Deterministic, scriptable, headless |
| Overlays/titles | ASS subtitles via ffmpeg for v1; Remotion (React) later for motion graphics | Start simple, upgrade when designs need motion |
| NLE handoff | OpenTimelineIO → FCPXML / Resolve import; optional Resolve scripting API for grade application | Keeps a professional finishing path |

### 3.2 Data model (core tables)

- `workspace` — client or channel; owns looks, music, assets.
- `asset` — one source file: path/URL, checksum, ffprobe metadata, camera, color space,
  capture time, GPS, ingest status.
- `shot` — segment of an asset: in/out, proxy URL, thumbnails, quality scores, motion
  stats, duplicate-group id, embedding (vector), labels (JSONB), favorite/reject flags.
- `transcript_segment` — asset, start/end, text, words with timestamps, speaker,
  energy score.
- `label_vocab` — controlled vocabulary (subject, place, time-of-day, move, size, mood)
  so the agent searches with known terms.
- `look` — versioned grade + brand recipe (see 2.2), LUT file references.
- `music_track` — file, BPM, beats array, mood tags, license note.
- `project` — workspace, brief/prompt history, target specs.
- `edit` — project, version, EDL JSON, agent reasoning summary, status.
- `render` — edit version, preset (proxy/final), aspect, output URL, duration, logs.
- `comment` — edit version, timestamp, author, text, resolved flag.
- `job` — type, payload, status, progress, worker, error.

### 3.3 EDL format (the brain ↔ renderer contract)

```json
{
  "version": 1,
  "project_id": "…",
  "look_id": "…",
  "output": { "aspect": "9:16", "width": 1080, "height": 1920, "fps": 24, "duration": 30.0 },
  "music": { "track_id": "…", "in": 12.4, "gain_db": -3, "duck_under_speech": true },
  "tracks": {
    "video": [
      { "shot_id": "…", "in": 3.2, "out": 5.9, "speed": 1.0, "reframe": "auto-subject",
        "transition_in": { "type": "cut" }, "grade_override": null }
    ],
    "speech": [ { "asset_id": "…", "in": 41.0, "out": 52.3 } ],
    "titles": [ { "t": 0.5, "dur": 2.5, "style": "hero", "text": "Aurelia, Santorini" } ],
    "end_card": { "template": "logo-fade", "dur": 2.0 }
  }
}
```

Human-readable, diffable, and every ID must exist in the catalog (validated before
render). Vlog edits add `chapters` and `captions`.

### 3.4 Edit agent design

- Tools: `search_shots(query, filters, k)`, `similar_shots(shot_id)`, `get_shot(id)`,
  `search_transcript(query)`, `music_beats(track_id)`, `propose_edl(edl)`,
  `validate_edl(edl)`, `render_preview(edl)`.
- The agent cannot reference a shot it did not retrieve via a tool call; the validator
  rejects unknown IDs and duplicate-group collisions.
- A **style card** per workspace (pacing, shot-size mix, do/don't list learned from your
  approvals and rejections) is injected into the prompt.
- Every revision is a new EDL version; comments on a draft are translated into
  constraints for the next version.
- Cost control: catalog search runs locally (pgvector); the LLM only sees compact shot
  summaries (ID, labels, scores, duration), not frames, during assembly.

### 3.5 Color pipeline

1. On ingest, detect the camera transfer function (metadata, filename pattern, or user
   mapping per camera profile).
2. Normalize: apply the camera's technical LUT to a working space (Rec.709 for v1;
   ACES later if needed).
3. Match: optional per-shot exposure/white-balance alignment to the Look's reference
   frame (histogram/mean-std matching on the proxy, then applied as `eq`/`colorbalance`
   params to the full-res).
4. Create: apply the Look's creative LUT at its strength, then grain/vignette.
5. Preview grades on the proxy with a side-by-side/wipe compare; batch-apply to a
   selection; "re-grade whole project" is a single job.
6. Export the resolved per-shot grade as metadata so Resolve import carries it.

### 3.6 Mobile strategy

- Installable PWA, responsive layouts; no native app in v1.
- Phone gets: library browsing (thumbnails + HLS proxies), search, favorites, prompt
  chat, draft review with timestamped comments, approve/reject, push notifications
  via web push when a render finishes.
- Phone never touches originals; all heavy work is queued to the desktop/worker.
- If a native app becomes worthwhile (camera-roll upload, offline), wrap with Capacitor
  later without changing the web code.

---

## 4. Phased roadmap

Each phase ends with something you use for real.

### Phase 0 — Decisions and setup (1 week)
- Confirm cameras and formats (log profiles?), footage volume, where footage lives.
- Stand up repo, Docker Compose (Postgres, Redis, API, worker, web), storage layout.
- Collect 2–3 hours of representative footage from each line (hotel, cabin, jewelry).

### Phase 1 — Ingest, catalog, search (2–3 weeks)
- ffprobe + shot detection + proxies + thumbnails + HLS.
- Vision labeling, transcription, embeddings, quality scores, duplicate grouping.
- Library UI with filters, text/semantic search, favorites/rejects, batch tagging.
- **Done when:** you can ask "misty lake drone shots, sharp, dawn" and get the right shots.

### Phase 2 — Looks and grading (2 weeks)
- Look model, LUT upload, camera profiles, preview with compare wipe.
- ffmpeg grade pipeline on proxies and full-res; batch re-grade.
- Shot matching to a reference frame.
- **Done when:** a hotel look applied across mixed clips reads as one consistent piece.

### Phase 3 — Postcard mode (3 weeks)
- Music library with beat detection.
- Edit agent with catalog tools, EDL validator, proxy renderer, final renderer
  (9:16, 16:9, 4:5, 1:1 with auto-reframe).
- Titles/end cards via Look templates.
- Chat-based revision loop; edit version history.
- **Done when:** one prompt produces a reel you would post for a hotel with ≤ 2 revision rounds.

### Phase 4 — Vlog mode (3 weeks)
- Transcript-driven select scoring, silence/filler removal, b-roll matching.
- Chapters, captions (burned + .srt), music ducking, intro/outro.
- OTIO/FCPXML export to Resolve.
- **Done when:** a week of cabin footage becomes a reviewable 10-minute cut in under an hour of your time.

### Phase 5 — Mobile review and remote access (1–2 weeks)
- PWA polish, HLS review player, timestamped comments → revision prompts, web push.
- Tailscale or cloud-fronted access; R2 for proxies if away from home.
- **Done when:** you can approve a render from your phone on the road.

### Phase 6 — Learning and scale (ongoing)
- Style cards learned from approvals/rejections; A/B drafts.
- Multi-workspace client management, deliverable presets per platform.
- Optional: Remotion motion graphics, Resolve scripting for grade handoff, AI upscale
  for older footage, direct publishing to Instagram/TikTok/YouTube.

---

## 5. Cost and performance notes

Rough per-hour-of-footage processing on a desktop with a mid-range GPU:

| Step | Time | External cost |
|------|------|---------------|
| Proxies + thumbnails + shot detection | ~10–15 min | none |
| Transcription (faster-whisper, GPU) | ~3–5 min | none |
| Embeddings + quality scores | ~2–3 min | none |
| Vision labeling (1 frame / shot, ~300 shots) | ~5 min | low single-digit dollars with a Haiku-class model |
| Edit planning per draft | ~30–90 s | cents to tens of cents per draft |
| Final render (1080p, 30 s reel) | ~1–2 min | none |

Storage: originals dominate; proxies add roughly 5–8 % of original size. Plan for a
dedicated drive or bucket per year of footage.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Agent picks technically fine but boring shots | Quality + "interest" scores (motion, faces, contrast), variety constraints, style card from your approvals |
| Grades look different per camera | Camera profiles with technical LUTs; shot matching to a reference frame; always preview on proxies first |
| ffmpeg renders look less polished than an NLE | Keep titles simple in v1; OTIO export for finishing; Remotion later for motion graphics |
| Long log clips bloat labeling cost | Label per shot (not per second), sample 1–3 frames, use a cheap model first and escalate only on low confidence |
| Phone playback of large files | Only HLS proxies on mobile; originals never leave the desktop/bucket |
| Music licensing for hotel deliverables | Music library tracks carry a license field; agent only picks licensed tracks for client workspaces |
| Hallucinated clip references | Tool-constrained agent + EDL validator |

---

## 7. Decisions needed from you

1. **Cameras and formats.** Which cameras (and log profiles) shoot the hotel work, the
   cabin, and the jewelry? This determines the color pipeline.
2. **Where the footage lives.** Local drive/NAS at the desktop, or already in a cloud
   bucket? Roughly how many TB now and per year?
3. **Desktop hardware.** GPU? This decides whether Whisper/embeddings run locally or
   on a cloud worker.
4. **Finishing path.** Is "good enough to post" from ffmpeg acceptable for hotel
   deliverables, or do you expect to finish every client piece in Resolve?
5. **Deliverable specs.** Which platforms/aspect ratios matter first (Reels 9:16,
   YouTube 16:9, both)? Captions required on vlogs?
6. **Music.** Do you have a licensed library (Artlist/Epidemic) or should the system
   manage one?
7. **Scope of v1.** Postcard mode first (hotels), vlog mode first (cabin), or both
   in parallel? Recommendation: Postcard first, since it exercises the whole pipeline
   with the shortest feedback loop.
8. **Repository.** This product should live in its own repo (e.g. `cinematic-editor`)
   rather than inside `trading-suite`; confirm and I'll scaffold it there.

---

## 8. Suggested repo layout (for Phase 0)

```
cinematic-editor/
  apps/
    web/            # Next.js PWA (desktop + phone)
    api/            # FastAPI: auth, catalog, looks, projects, EDL, jobs
    worker/         # ingest, label, transcribe, embed, beats, render
  packages/
    edl/            # EDL schema + validator (shared Python/TS types)
    looks/          # Look schema, LUT handling, camera profiles
    agent/          # edit-agent prompts, tools, style cards
  infra/
    docker-compose.yml   # postgres, redis, api, worker, web
  luts/             # technical + creative LUTs
  docs/
```
