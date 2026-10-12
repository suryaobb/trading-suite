# Kickoff package — how to hand this build to Claude

Three pieces:

1. **CLAUDE.md** — paste into the root of the new repo so every session starts with the
   same context (section A).
2. **First prompt** — paste into a Claude Code Project thread to run Phase 0 and Phase 1
   (section B).
3. **Follow-on prompts** — one per later phase (section C).

Attach `PLAN.md` from this folder to the Project (or commit it to the new repo under
`docs/PLAN.md`) so Claude can read the full plan.

---

## A. CLAUDE.md for the new repo

```markdown
# Cinematic Editor

Prompt-driven editing system that turns a labeled library of real cinematic footage
into finished, color-graded videos. Full plan: docs/PLAN.md. Read it before any work.

## What we are building
- Postcard mode: 15–90 s music-driven, graded reels for hotels and travel.
- Vlog mode: 4–20 min transcript-driven narrative edits (cabin build, jewelry making).
- Desktop is the workstation. Phone (installable PWA) is for browsing, prompting,
  reviewing drafts, and approving renders.
- Real footage only. No AI-generated video in v1.

## Stack (do not change without asking)
- Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic.
- Workers: Python, RQ on Redis. ffmpeg/ffprobe, PySceneDetect, faster-whisper,
  open_clip (SigLIP/CLIP), OpenCV, librosa, OpenTimelineIO.
- DB: Postgres 16 + pgvector.
- Frontend: Next.js (App Router), React, TypeScript, Tailwind, PWA, hls.js.
- AI: Anthropic SDK. Haiku-class model for bulk frame labeling, Opus/Fable-class for
  edit planning. Model IDs live in one config file, never hardcoded elsewhere.
- Infra: Docker Compose (postgres, redis, api, worker, web). Runs on the owner's desktop.
- Storage: local path or S3-compatible bucket behind one storage interface.

## Layout
apps/web, apps/api, apps/worker, packages/edl, packages/looks, packages/agent,
infra/, luts/, docs/

## Rules
- The EDL (packages/edl) is the contract between the edit agent and the renderer.
  Every change to it needs a schema bump, validator update, and a test.
- The edit agent may only reference shot IDs returned by its tools. The validator
  rejects anything else.
- Never run heavy jobs in the API process. Everything over ~1 s goes to a worker job
  with progress reporting.
- Proxies are 720p H.264 + HLS. Originals are never served to the phone.
- Every phase ends with a runnable demo and a short docs/phases/<n>.md note on what
  works, what does not, and how to run it.
- Tests: pytest for Python, vitest for web. Run them before every commit.
- Commit small, descriptive commits. Open a PR per phase.
- Ask before adding a paid service or a dependency over 50 MB.

## Owner's footage and preferences
(Fill in after Phase 0: cameras, log profiles, storage path, GPU, aspect ratios,
music library, finishing path, per-workspace style notes.)
```

---

## B. First prompt (Phase 0 + Phase 1)

```text
You are building the Cinematic Editor described in docs/PLAN.md. Read PLAN.md and
CLAUDE.md fully before doing anything.

My answers to the plan's open questions:
1. Cameras / log profiles: <e.g. Sony A7IV S-Log3, DJI Mini 4 Pro D-Log M, iPhone 15 Pro Apple Log>
2. Footage location and size: <e.g. /Volumes/Footage on the desktop, ~6 TB now, ~3 TB/year>
3. Desktop: <e.g. Mac Studio M2 Max 64 GB / Windows + RTX 4080>
4. Finishing: <ffmpeg output is fine for most; Resolve export for hero pieces>
5. Deliverables: <9:16 Reels first, 16:9 YouTube second; burned captions on vlogs>
6. Music: <Artlist subscription / none yet>
7. Start with: Postcard mode (hotels).
8. This is its own repo.

If any answer above is still a placeholder in angle brackets, use the plan's default
and note the assumption in docs/phases/0.md.

Do Phase 0 and Phase 1 from the plan:

Phase 0
- Scaffold the monorepo layout from PLAN.md section 8.
- Docker Compose with postgres (pgvector), redis, api, worker, web. `make up` brings
  everything up; `make test` runs all tests.
- Alembic migrations for the core tables in PLAN.md section 3.2.
- A storage interface with two backends: local filesystem and S3-compatible.
- Write docs/phases/0.md describing how to run it.

Phase 1 — ingest, catalog, search
- `cinematic ingest <path>` CLI and an API endpoint that enqueue ingest jobs.
- Per file: ffprobe metadata, PySceneDetect shots, 720p proxy + HLS, thumbnails and
  contact sheet per shot, faster-whisper transcript with word timestamps, quality
  scores (sharpness, clipping, stability), SigLIP embeddings per shot, near-duplicate
  grouping, vision labels via Claude using the controlled vocabulary in label_vocab.
- Camera profile detection from metadata and filename patterns for the cameras I
  listed; store the detected transfer function on the asset.
- Web app: library grid with filters (workspace, label, date, quality), text and
  semantic search, "find similar", favorites/rejects, batch tagging, shot detail with
  proxy playback. Must work at phone width.
- Tests for the EDL package skeleton, the ingest pipeline on a 30-second sample clip
  (generate a synthetic test clip with ffmpeg so tests need no real footage), and
  the search endpoints.
- docs/phases/1.md with a demo script.

Working style
- Work in small PRs, one per major piece (scaffold, migrations, ingest worker,
  labeling, web library). Tell me when each is ready to review.
- When you need my desktop (real footage, GPU timing), say so explicitly and give me
  the exact commands to run, or request a Remote Control thread.
- Stop and ask before adding any paid service beyond the Anthropic API.
- Phase 1 is done when I can type "misty lake drone shots, sharp, dawn" into the
  library and get the right shots from my footage.
```

---

## C. Follow-on prompts

**Phase 2 — Looks and grading**
```text
Read docs/PLAN.md sections 2.2 and 3.5 and docs/phases/1.md. Build Phase 2: the Look
model and editor, LUT upload (.cube), camera profiles with technical LUTs for my
cameras, reference-frame shot matching, ffmpeg grade pipeline on proxies and
originals, a compare-wipe preview, and batch re-grade as a worker job. Done when a
hotel Look applied across clips from all my cameras reads as one consistent piece.
Deliver docs/phases/2.md.
```

**Phase 3 — Postcard mode**
```text
Read docs/PLAN.md sections 2.3, 3.3, 3.4 and docs/phases/2.md. Build Phase 3: music
library with librosa beat detection, the edit agent with catalog tools and the EDL
validator, proxy renderer and final renderer for 9:16, 16:9, 4:5 and 1:1 with
auto-reframe, title and end-card templates from the Look, the chat revision loop, and
edit version history. Done when one prompt produces a hotel reel I would post with at
most two revision rounds. Deliver docs/phases/3.md.
```

**Phase 4 — Vlog mode**
```text
Read docs/PLAN.md section 2.4 and docs/phases/3.md. Build Phase 4: transcript-driven
select scoring, silence and filler removal, b-roll matching to speech, chapters,
burned and .srt captions, music ducking, intro/outro, and OTIO/FCPXML export to
DaVinci Resolve. Done when a week of cabin footage becomes a reviewable 10-minute cut
in under an hour of my time. Deliver docs/phases/4.md.
```

**Phase 5 — Mobile review and remote access**
```text
Read docs/PLAN.md sections 2.5 and 3.6 and docs/phases/4.md. Build Phase 5: PWA
polish, HLS review player with timestamped comments that feed back into revision
prompts, approve/reject flow, web push on render completion, and a documented
Tailscale setup (plus optional R2 for proxies). Done when I can approve a render from
my phone away from home. Deliver docs/phases/5.md.
```
