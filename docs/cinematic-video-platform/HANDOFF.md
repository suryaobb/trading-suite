# Cinematic Editor — Session Handoff

Prepared 2026-11-08 for a new Claude Code session. Read this first, then PLAN.md,
then KICKOFF.md. All three are in this document.

## 1. What this project is

A prompt-driven editing platform for real cinematic footage. Owner: Surya
(suryaobb on GitHub). Two product lines on one engine:

- **Postcard mode**: 15–90 s music-driven, color-graded reels for hotels and travel.
- **Vlog mode**: 4–20 min transcript-driven narrative edits of the owner building a
  cabin and making jewelry.

Desktop is the workstation; the phone (installable PWA) is for browsing, prompting,
reviewing drafts, and approving renders. Real footage only; no AI-generated video in v1.

## 2. State of the work so far

- A full product and technical plan exists (PLAN.md, Part B below).
- A kickoff package exists with a CLAUDE.md template and per-phase prompts
  (KICKOFF.md, Part C below).
- Both were committed to branch `claude/cinematic-video-editing-plan-m6oir3` in
  `suryaobb/trading-suite` but **could not be pushed**: the Claude GitHub App has no
  access to that repository. Fix before any coding session, at
  https://claude.ai/connect-github or
  https://github.com/apps/claude/installations/select_target.
- Decision taken: the product gets **its own repository** (suggested name
  `cinematic-editor`), not trading-suite.
- Decision taken: build **Postcard mode first**.

## 3. What is known about the owner's footage

Found in the owner's Google Drive (connector was attached to the planning session):

| Folder / files | Camera (inferred) | Notes |
|---|---|---|
| `DJI_20241018194xxx_06xx_D.MP4` (Oct 2024, ~40–70 MB each) | DJI drone | `_D` suffix usually means D-Log; confirm with ffprobe (color transfer / `color_trc`). |
| `Hotel + Spa.mov`, `SPA.mov`, `Hedonist's Guide.mov`, `New-Hedonist.mov` (Feb 2025, 10–30 MB) | Unknown, owner to confirm | Likely already-edited exports; check whether they are source or finished pieces. |

Open questions the owner still needs to answer (from PLAN.md section 7):
cameras and log profiles for each line, footage location and volume, desktop GPU,
finishing path (ffmpeg vs DaVinci Resolve), deliverable aspect ratios and captions,
music library and licensing.

## 4. Intake paths for footage

**Google Drive (works from a phone)**
- Owner creates one folder, e.g. "Cinematic Editor Inbox", shares it as
  "anyone with the link, viewer", and uploads clips.
- The Drive connector lists files, but its download tool returns base64 inline, which
  is unusable for video. Download with curl instead:
  `https://drive.google.com/uc?export=download&id=<fileId>` (large files redirect to
  `drive.usercontent.google.com` and may need the confirm-token dance).
- A personal cloud environment named **cinematic-editor** was created and approved.
  It allows `drive.google.com` and `drive.usercontent.google.com`, keeps package
  registries and MCP servers reachable, and has a setup script
  `pip install -q scenedetect opencv-python-headless`. The script is unproven until a
  fresh session starts on that environment. Start new sessions on it.

**Remote Control (best for real volume)**
- In the Claude desktop app: Settings → Claude Code → Remote Control → add the footage
  folder. A cloud session can then attach to that folder and run ffmpeg, Whisper, and
  labeling on the owner's machine with the originals in place.
- A cloud session attaches to one folder at a time and stays tied to the first
  computer it attaches to. Multiple Remote Control sessions can run on one computer;
  use worktree mode for parallel code sessions.

**Cloud container facts** (planning session): ffmpeg 6.1, Python 3, 4 CPUs, 15 GB RAM,
no GPU, ~30 GB free disk. Fine for tens of clips per day, not terabytes.

## 5. Day-one deliverable ("Phase 1 lite")

Before the full stack, produce something the owner can review:

1. Pull every clip; ffprobe metadata; detect camera and log profile; split long
   recordings into shots (PySceneDetect).
2. 720p proxy, thumbnails, and a contact sheet per shot.
3. Label each shot with a vision model using the controlled vocabulary in PLAN.md
   (subject, place, time of day, weather, camera move, shot size, mood, people, text on
   screen) plus a quality score; group near-duplicate takes.
4. Output `catalog.json` + `catalog.csv` and one self-contained `library.html` with
   thumbnails, filters by label and quality, and search.
5. Review labels with the owner and fix the vocabulary. That becomes ground truth for
   the real catalog in Phase 1.

## 6. How to run the build

Recommended: a Claude Code Project at claude.ai/code with the new repo attached, one
thread per phase. If Projects is not available on the account, one cloud session per
phase on the cinematic-editor environment, pasting the phase prompt from KICKOFF.md.
Use a Remote Control thread for anything that needs real footage or the desktop GPU.
A regular claude.ai Project (mobile app) is fine for planning and prompt writing, but
cannot build.

## 7. Working rules carried over

- The EDL JSON is the contract between the edit agent and the renderer; the agent may
  only reference shot IDs returned by its tools, and a validator rejects anything else.
- Heavy work always runs in a worker job, never in the API process.
- Phone only ever sees proxies/HLS, never originals.
- Each phase ends with a runnable demo and a `docs/phases/<n>.md` note.
- Ask before adding any paid service beyond the Anthropic API.
- Never put model identifiers in commits, PR text, or code comments.
