# Medal sorter — username pass

Local, read-only first pass for Medal clips. It scans `W:\medal\Clips`, links matching files in `W:\medal\Edits`, samples source-video frames, runs local OCR, and maps detected text to names you supply.

It never renames, moves, edits, or writes inside `W:\medal`.

## Setup

Python is managed by `uv`; no system Python needed.

```powershell
cd E:\GitHub\medal_sorter
uv python install 3.12
uv sync
uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
uv run medal-sorter init
```

`pyproject.toml` uses the PyTorch CUDA 12.6 wheel index. Expected GPU check: `True NVIDIA GeForce RTX 2060`. If it shows `False`, update the NVIDIA driver or run with `--cpu`.

First EasyOCR run downloads models into this workspace's `data\easyocr_models`; it does not use Medal folders.

## Add friend groups

The friend config is YAML only. Groups are top-level sections and each known name is listed under its group.

```yaml
schema_version: 1
ignored_ocr_text:
  - Play
  - Settings
  - Headshot
matching:
  fuzzy_threshold: 90

friday_friends:
  - AlexDiscord
  - A1ex
  - AlexOnSteam

raid_team:
  - BigTank
  - Healer123
  - DPS_Zero
```

Strings under each group are treated as known names. If you want a canonical name plus aliases, use a mapping:

```yaml
mygroup:
  - "Alex Discord": ["A1ex", "AlexOnSteam"]
```

`friends.example.yaml` shows the supported shape.

Known-name matching is deliberate. Full-screen OCR sees HUD labels, menus, scores, and chat. Matching against your supplied names gives useful group results without pretending arbitrary UI text is a username. Names shorter than five characters require an exact OCR match to limit false positives.

## Run

Benchmark five clips first:

```powershell
uv run medal-sorter usernames --medal-root W:\medal --limit 5
```

Then run all sources:

```powershell
uv run medal-sorter usernames --medal-root W:\medal

# Print source clips under detected friend groups.
uv run medal-sorter report
```

Useful variants:

```powershell
# One Medal game folder. Exact name.
uv run medal-sorter usernames --game "Marvel Rivals"

# Show catalog and planned OCR. No write.
uv run medal-sorter usernames --dry-run

# Re-OCR selected videos. Normal runs skip current results.
uv run medal-sorter usernames --game "Marvel Rivals" --force

# OCR raw screen text before adding friend mappings. No username resolution value yet.
uv run medal-sorter usernames --limit 5 --collect-raw-text
```

Default sampling: evenly spaced frames, roughly every four seconds, at most eight frames per clip. Run five clips first. Tune `--sample-interval` and `--max-samples` only after checking the stored evidence.

## Persistent files

`clip_data.json` is the app-owned database. Keys are case-folded paths relative to `W:\medal`, so changing a drive letter does not break records. Each source record stores:

- File size and modification-time fingerprint.
- Linked `Edits` paths.
- Raw OCR text, timestamp, confidence, and bounding box.
- Resolved usernames, groups, scores, and evidence.
- Reserved `clip_type`, `trim`, and `audio_channels` fields.

`friends.yaml` is the app-owned name/group input mapping. Changing it recomputes matches from saved OCR observations; it does not re-read video frames. Source file changes, OCR sampling changes, pipeline updates, failed runs, and `--force` trigger only needed reanalysis.

Writes to `clip_data.json` are atomic after discovery, every five clips, and at completion. Use `--checkpoint-every 1` if each clip needs a checkpoint. Empty username arrays mean analysis completed with no match; they are not retried endlessly.

## Medal edits

Source clips are the only videos OCR'd. Edits inherit their source record. The linker recognizes `--edit`, `--tr-edit`, `-tr-edit`, `-trim-<timestamp>`, and suffix variants such as `--edit-7fb6`. It only links an exact source basename; duplicate or missing source names remain `unresolved` in `clip_data.json` and are never guessed.

Current library check: 936 source MP4s, 135 edit MP4s; 94 edits use a recognizable matching source basename. Unlinked edits remain visible but are skipped, avoiding accidental duplicate OCR.

## Limits of first pass

This pass does not classify clips, trim them, inspect audio, alter Medal metadata, or upload video. It is generic full-frame OCR. Game-specific HUD crops and unknown-name discovery should follow only after the five-clip benchmark shows which game UIs and name formats matter.

## Clip triage UI

A phone-friendly quick-review interface lives in `triage/`. It reads
`clip_data.sqlite` read-only and stores its own state (collections, discard
flags) in separate `triage_*` tables, never touching medal-sorter's tables.

```powershell
cd E:\GitHub\medal_sorter
uv run triage/triage.py
```

The Medal root defaults to `W:\medal`, matching the `medal-sorter` commands.
Pass `--medal-root <path>` only if your Medal folder is elsewhere.

Then open [http://localhost:8765](http://localhost:8765) on this PC. On your phone,
use the PC's LAN address (same LAN, no auth). Pick a
game, then a friend group, and review clips one at a time chronologically —
file them into your own collections or mark Keep/Delete. Both mark actions
advance to the next clip. The default-on "Skip marked" checkbox hides marked
clips; turn it off to revisit or undo a mark. Delete is mark-only; no files are
moved or deleted. When a clip has a linked edit, the edit is shown and the raw
source stays hidden unless toggled. Stdlib only, one file, landscape supported.
