# Clip triage UI

Phone-friendly quick review for Medal clips, TikTok/Reels-style. It reads
`clip_data.sqlite` but never writes to medal-sorter's tables; all triage
state (collections, discard flags) lives in its own `triage_*` tables in the
same database file.

## Run

```powershell
cd E:\GitHub\medal_sorter
uv run triage/triage.py --workspace E:\GitHub\medal_sorter --medal-root W:\medal
```

Then open `http://<pc-ip>:8765` on your phone (same LAN, no auth). Desktop
works too.

## Flow

1. Pick a game, then a friend group (groups narrow to the selected game).
2. Clips play one at a time, chronological, autoplaying muted on loop.
   Unmute once via the player controls and the setting sticks for later clips.
3. File a clip into any of your own collections (toggle buttons, tap again to
   remove), or mark it discard. Discard is mark-only; no files are moved or
   deleted, ever.
4. Hit Keep on a reviewed clip (with or without a collection) and it advances
   to the next one — kept clips stop appearing in the queue.
5. When a source clip has a linked edit, the edit is shown and the raw source
   is hidden unless you toggle "sources".

Everything is one file and stdlib-only, so it runs anywhere Python 3.12+
exists. Landscape mode switches to a side-by-side layout; the video always
fits the screen.
