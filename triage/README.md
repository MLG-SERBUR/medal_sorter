# Clip triage UI

Phone-friendly quick review for Medal clips, TikTok/Reels-style. It reads
`clip_data.sqlite` but never writes to medal-sorter's tables; all triage
state (collections, discard flags) lives in its own `triage_*` tables in the
same database file.

## Run

```powershell
cd E:\GitHub\medal_sorter
uv run triage/triage.py
```

The Medal root defaults to `W:\medal`, matching the `medal-sorter` commands.
Pass `--medal-root <path>` only if your Medal folder is elsewhere.

Then open [http://localhost:8765](http://localhost:8765) on this PC. On your phone,
use the PC's LAN address (same LAN, no auth). Desktop
works too.

## Flow

1. Pick a game, then a friend group (groups narrow to the selected game).
2. Clips play one at a time, chronological, autoplaying with sound on loop
   when the browser permits autoplay with sound. If the browser blocks it,
   start playback with the player controls.
3. File a clip into any of your own collections (toggle buttons, tap again to
   remove), or mark it for deletion. Delete is mark-only; no files are moved or
   deleted, ever.
4. Keep and Delete both advance to the next clip. The default-on "Skip marked"
   checkbox hides clips marked Keep or Delete; turn it off to revisit or undo a
   mark, then turn it back on to skip marked clips again.
5. When a source clip has a linked edit, the edit is shown and the raw source
   is hidden unless you toggle "sources".

Everything is one file and stdlib-only, so it runs anywhere Python 3.12+
exists. Landscape mode switches to a side-by-side layout; the video always
fits the screen.
