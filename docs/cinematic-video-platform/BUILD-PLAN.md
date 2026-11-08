# Cinematic Editor — Full-Scale Build Plan (v0.2)

Status: **awaiting go/no-go**. Supersedes PLAN.md where they differ; PLAN.md remains the
reference for the data model, color pipeline and EDL contract.

## 0. What changed since v0.1

v0.1 was a prompt-to-reel generator. v0.2 is a product with three faces on one engine:

1. **Library** — every clip ingested, split into shots, labeled, searchable (built and
   tested in `tools/cinematic/ingest_lite.py`; 3 clips → 38 labeled shots).
2. **Creative Director** — a chat that understands the footage and cinematic styles.
   "I have these clips from camping, what do you suggest?" → 3–5 concrete video ideas
   (format, length, mood, music, structure, which shots) → pick one or all → it makes them.
3. **Editor** — a CapCut-class timeline editor in the browser so any AI draft can be
   finished by hand: multi-track, trim/split, speed, transitions, text, filters/LUTs,
   music with beat markers, captions, aspect switch, export.

The glue is that **AI and human edit the same timeline**. The EDL (edit decision list)
is one JSON document; the Creative Director writes it, the editor shows and changes it,
the renderer turns it into video. Nothing is thrown away between "AI made a draft" and
"I tweaked it".

---

## 1. The experience, end to end

### 1.1 "I have clips from camping"

1. You drop a folder (Drive, Mac folder, phone upload) into a **Project** → ingest runs.
2. The Director reads the catalog, not the pixels: 212 shots, 3 locations (lakeshore,
   campsite, forest trail), golden hour + night fire, 2 people, drone 20 %, hand-held 60 %,
   one 4-minute talking clip, average quality 0.71, 14 near-duplicate groups.
3. It answers with ideas, each a card with a thumbnail strip:
   - **Postcard, 20 s, 9:16** — "Two nights at the lake": 12 slow shots, dusk-to-fire arc,
     warm film look, ambient track, one title.
   - **Reel, 45 s, 9:16** — "Camp setup to first coffee": beat-cut, 28 shots, whip
     transitions, upbeat acoustic, captions on 3 moments.
   - **Mini-vlog, 4 min, 16:9** — built around the talking clip: your words as the spine,
     b-roll matched to what you say, chapters, captions.
   - **Cinematic trailer, 30 s, 2.39:1 letterbox** — drone-led, slow motion, deep teal
     shadows, sparse title cards.
   - **Photo-dump carousel** — 10 stills pulled from the sharpest frames, graded to match.
4. You say "make 1 and 2" (or "all"). Drafts render as proxies in under a minute each.
5. You open a draft: watch, comment ("the fire shot is too dark", "swap shot 7"), or open
   the editor and do it yourself. The Director sees your edits and learns your taste for
   the next project.
6. Approve → final render in each requested aspect → download or publish.

### 1.2 Hotel work

Same flow with a **Workspace** per client holding its Look (LUT, type, logo, pacing), style
notes, and licensed music. Ideas come back already on-brand. Brand rules are hard
constraints the Director cannot break (no unlicensed music, logo end card, no watermark
shots, minimum shot quality).

### 1.3 Phone

Browse library, chat with the Director, review drafts, comment, approve, and a
**lite editor**: reorder/swap/trim shots, change text, switch look, change music. Full
multi-track editing stays on desktop.

---

## 2. Architecture

```
 ┌────────────────────────── Web app (Next.js, PWA) ──────────────────────────┐
 │  Library   │  Director chat + idea cards  │  Timeline editor  │  Review    │
 │            │                              │  (desktop) / lite │  & approve │
 └─────┬──────┴───────────────┬──────────────┴────────┬──────────┴─────┬──────┘
       │                      │                       │                │
       ▼                      ▼                       ▼                ▼
 ┌───────────────────────── API (FastAPI) ─────────────────────────────────────┐
 │ projects · catalog/search · EDL versions · looks · music · jobs · comments  │
 └───┬───────────────┬────────────────┬──────────────────┬─────────────────────┘
     ▼               ▼                ▼                  ▼
 Postgres+pgvector   Object store     Redis queue        Director service
 (catalog, EDLs,     (originals,      (jobs)             (Claude + tools:
  looks, style       proxies, HLS,       │                search_shots, scene_graph,
  library, taste)    renders, LUTs)      ▼                style_library, propose_edl,
                                   Workers (Python)      validate_edl, render_preview,
                                   ingest · label ·      critique_draft)
                                   transcribe · embed ·
                                   beats · render(ffmpeg)
```

### 2.1 Key design decisions

| Decision | Choice | Why |
|---|---|---|
| One timeline format | EDL v1 JSON (PLAN.md 3.3) extended with tracks, keyframes, transitions, text layers, audio ducking, captions | AI, editor and renderer all speak it; versioned, diffable |
| Preview vs final | Browser preview composites 720p proxies live (video element + canvas/WebGL for filters, text, transitions); final render is server-side ffmpeg from the EDL | Instant scrubbing without a render farm; deterministic output |
| Editor base | Evaluate the MIT-licensed open-source browser editors first (OpenCut, omniclip, Etro.js-style engines). Fork the best fit if its timeline + preview core is solid; otherwise build the timeline on React + Zustand + canvas. Decision gate at M3 start | A CapCut-class timeline is the riskiest part; reuse buys months |
| Director | Claude with strict tool use; sees shot summaries and a scene graph, never raw frames at planning time; vision only at labeling and critique | Cost control and no hallucinated clips |
| Style knowledge | A **style library** (data, not prompt text): formats × moods → pacing curves, shot-structure templates, typography, grade, music tempo range, transition vocabulary. Starts at ~12 formats, grows from your approvals | Makes ideas concrete and consistent; editable without code |
| Taste model | Per-workspace "taste" rows: approvals, rejections, manual edits diffed against drafts | The Director gets better on your footage specifically |
| Run location | Docker Compose on the Mac; phone via Tailscale; optional R2 + small VM for away-from-home | Footage stays local; no cloud GPU bill |

### 2.2 Editor feature set (CapCut parity target)

**v1 (M3):** multi-track timeline (video ×3, audio ×2, text, overlay), magnetic snapping,
trim/split/ripple delete, drag reorder, speed (constant + ramp), transitions (cut, dissolve,
dip, wipe, whip-zoom, blur), text presets with animations, filters/LUT per clip or global,
color adjustments (exposure, contrast, saturation, temperature, tint), music with beat
markers and snap-to-beat, volume keyframes + auto-duck, aspect switch (9:16, 16:9, 4:5, 1:1,
2.39:1) with auto-reframe, captions (auto from transcript, styled), stickers/logo overlay,
undo/redo, keyboard shortcuts, export presets (Reels, TikTok, YouTube, 4K master).

**v2 (M6+):** keyframe animation for scale/position/rotation/opacity, masks, chroma key,
speed curves with optical flow, audio extraction/denoise, templates you save from any
edit, multi-user comments on the timeline.

### 2.3 Scene understanding

Ingest already produces per-shot labels and quality. Add:
- **Scene graph**: cluster shots by location (embedding + labels + capture time), then by
  time of day; detect "moments" (people laughing, fire lighting, drone reveal) from motion,
  faces and audio energy.
- **Narrative beats**: for each scene, candidates for hook, establishing, detail, peak,
  resolve — the Director fills a format template from these.
- **Best-interval**: within a long shot, the 1–4 s window with highest stability × interest.
- **Style classifier**: what the footage itself leans toward (moody/bright, slow/fast,
  people/landscape) so ideas fit the material rather than fighting it.

---

## 3. Milestones

Estimates assume one AI-assisted developer working daily, with you reviewing a few times
a week. Each milestone ends with something you can use.

| # | Milestone | Weeks | You get |
|---|---|---|---|
| M0 | Foundation | 1 | New repo, Docker Compose, shared EDL schema (Python + TS), design system, CI, CLAUDE.md |
| M1 | Library | 2–4 | Ingest workers (productionized ingest_lite + transcription + embeddings + scene graph), library UI with search, Drive/Mac/phone upload |
| M2 | Creative Director v1 | 4–6 | Chat → 3–5 idea cards → one-click drafts; style library (12 formats); 6 looks; music library + beats; proxy render < 60 s |
| M3 | Timeline editor v1 | 6–11 | CapCut-core editor on the same EDL; "ask the Director to change this" inside the editor; final render + export presets |
| M4 | Mobile + review | 11–13 | PWA lite editor, review/comment/approve, push notifications, Tailscale/R2 access |
| M5 | Vlog mode | 13–16 | Transcript-driven cutting, captions styles, b-roll matching, chapters, Resolve export |
| M6 | Taste + scale | 16+ | Learning from your edits, saved templates, per-client brand kits, publishing to Instagram/TikTok/YouTube, keyframes/masks |

**MVP you can post from: end of M3 (~11 weeks).** Postcards and reels with hand finishing.
**Full v1: end of M5 (~16 weeks).**

### 3.1 M0–M2 in detail (what starts the day you say go)

**M0 (week 1)**
- Create `cinematic-editor` repo; monorepo layout from PLAN.md §8; Docker Compose
  (postgres+pgvector, redis, api, worker, web); Makefile; CI (pytest, vitest, lint).
- `packages/edl`: JSON schema v1 with tracks/clips/transitions/text/audio/captions,
  Pydantic + TypeScript types generated from one source, validator, 20 fixture tests.
- Design tokens + component kit (dark UI, phone-first layouts).
- Port `ingest_lite.py` and `render_lite.py` into `apps/worker` as the first jobs.

**M1 (weeks 2–4)**
- Jobs: probe → shots → proxy/HLS → thumbs → quality → transcript (faster-whisper) →
  embeddings (SigLIP) → labels (Claude vision, controlled vocabulary) → duplicates →
  scene graph. Progress streamed to the UI.
- Sources: Drive folder (link or OAuth), Mac folder (watch), phone upload (PWA).
- Library UI: grid, filters, semantic search, "find similar", favorites/rejects,
  batch tags, shot detail with proxy player and labels you can correct (corrections
  feed the taste model).

**M2 (weeks 4–6)**
- Style library v1 (data files): postcard, reel, cinematic trailer, mini-vlog,
  day-in-the-life, before/after, montage, photo dump, hotel room tour, amenity spotlight,
  destination teaser, product/jewelry macro story. Each: duration range, aspect,
  pacing curve, shot-structure template, text style, transition vocabulary, music tempo
  and mood, grade suggestion.
- Director tools: `describe_footage`, `search_shots`, `scene_graph`, `style_library`,
  `music_search`, `propose_ideas`, `build_edl(idea)`, `validate_edl`, `render_preview`,
  `critique_draft` (vision pass on the rendered proxy to catch dark/soft/duplicate shots).
- Chat UI with idea cards, "make this / make all", draft gallery with version history.
- Looks v1: 6 looks (warm film, clean bright, moody teal, golden hour, monochrome,
  neutral) + per-camera input transforms; reference-frame matching.
- Music: your licensed library ingested with BPM/beats/mood; snap-to-beat cutting.

---

## 4. Risks

| Risk | Plan |
|---|---|
| Timeline editor scope balloons | Hard v1 feature list (§2.2); open-source base decision gate at M3 start; preview fidelity rule: "what you see is the proxy, final is ffmpeg", accepted |
| Browser preview can't match ffmpeg render exactly | Shared filter definitions (same LUTs, same curves in GLSL and ffmpeg); render a 3-second "truth check" when users change color |
| Director ideas feel generic | Ideas must cite specific shots and moments from *this* footage; style library is concrete; critique pass before showing a draft; taste model from your choices |
| Labeling cost on large libraries | Label per shot not per second, cheap model first, escalate only low-confidence; embeddings and quality are free and local |
| Mac performance for Whisper/embeddings | Apple Silicon runs both fine on CPU/Metal; batch overnight for big drops |
| Music licensing | Only tracks with a license field can be used in client workspaces; personal workspace can use anything you upload |
| Mobile editing complexity | Lite editor only: reorder, swap, trim, text, look, music; everything else desktop |

---

## 5. What I need from you before launch

1. **Go/no-go** on this plan and the M0–M3 MVP target.
2. **New repository.** Create `cinematic-editor` on GitHub (or tell me to build it inside
   trading-suite under `apps/cinematic-editor/`, not recommended) and add it to a Claude
   Code session; this session is scoped to trading-suite only.
3. **Anthropic API key** for the Director and labeling (I will ask for it securely
   through the credential form, never in chat), or confirm you want labeling done
   manually at first.
4. **Editor base**: OK to evaluate and fork an MIT-licensed open-source editor if it
   fits? (Recommended.)
5. **Mac details**: chip and RAM, free disk, where footage will live.
6. **First real footage** in the Drive Inbox so M1 is tested on real material from
   day one.

---

## 6. Launch sequence (first 48 hours after go)

1. Repo + Compose + CI + CLAUDE.md + EDL schema (M0 start).
2. Port ingest and render tools into workers; run them on the Inbox footage.
3. Library UI skeleton on phone and desktop; you see your clips in the product.
4. Daily: short status in chat, PR per piece, demo link at each milestone.
