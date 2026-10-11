#!/usr/bin/env python3
"""Clip triage UI for Medal clips — separate project from medal-sorter.

Reels-style swipe triage: pick game -> pick friend group -> swipe clips
chronologically, filing them into your own collections or marking for deletion.

Reads medal-sorter's clip_data.sqlite READ-ONLY (records/metadata tables are
never written). All triage state lives in our own triage_* tables in the same
file, keyed by medal-sorter's clip key (casefolded relative path).

    python triage.py --workspace <dir with clip_data.sqlite> --medal-root W:\\medal

Then open http://<pc-ip>:8765 on your phone (same LAN, no auth).

Stdlib only. No file operations: discard only marks. Clips autoplay muted on
loop; unmute once via the player controls and it sticks. Keep marks a clip as
reviewed so it stops showing up in the queue, advancing to the next clip.
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OWN_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS triage_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS triage_clip_collections (
    clip_key TEXT NOT NULL,
    collection_id INTEGER NOT NULL,
    PRIMARY KEY (clip_key, collection_id)
);
CREATE TABLE IF NOT EXISTS triage_flags (
    clip_key TEXT PRIMARY KEY,
    discard INTEGER NOT NULL DEFAULT 0,
    keep INTEGER NOT NULL DEFAULT 0,
    rated_at TEXT
);
"""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Triage:
    """Read-only view over medal-sorter's data + our own triage tables."""

    def __init__(self, db_path: Path, medal_root: Path):
        self.db_path = db_path
        self.medal_root = medal_root.resolve()
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(OWN_TABLES_SQL)
            # migrate older triage_flags tables missing the keep column
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(triage_flags)")}
            if "keep" not in cols:
                self.conn.execute(
                    "ALTER TABLE triage_flags ADD COLUMN keep INTEGER NOT NULL DEFAULT 0"
                )
            self.conn.commit()
        self._refresh()

    def _refresh(self):
        """Load clip records from medal-sorter's tables (read-only)."""
        with self.lock:
            rows = self.conn.execute("SELECT clip_path, data FROM records").fetchall()
            flags = {
                r["clip_key"]: dict(r)
                for r in self.conn.execute("SELECT * FROM triage_flags")
            }
            clip_cols = {}
            for r in self.conn.execute(
                "SELECT clip_key, collection_id FROM triage_clip_collections"
            ):
                clip_cols.setdefault(r["clip_key"], []).append(r["collection_id"])
            collections = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT id, name FROM triage_collections ORDER BY name COLLATE NOCASE"
                )
            ]
        clips = []
        for row in rows:
            key = row["clip_path"]
            try:
                rec = json.loads(row["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(rec, dict) or rec.get("kind") not in ("source", "edit"):
                continue
            rel = rec.get("path", "")
            parts = Path(rel).parts
            game = parts[1] if len(parts) >= 3 else "(unknown)"
            resolution = (
                rec.get("analysis", {}).get("usernames", {}).get("resolution", {})
            )
            friends = []
            if isinstance(resolution, dict):
                for u in resolution.get("usernames", []) or []:
                    if isinstance(u, dict) and u.get("name"):
                        friends.append(
                            {"name": u["name"], "group": u.get("group") or "(ungrouped)"}
                        )
            file_info = rec.get("file", {}) or {}
            clips.append(
                {
                    "key": key,
                    "path": rel,
                    "game": game,
                    "kind": rec.get("kind"),
                    "has_edits": bool((rec.get("links") or {}).get("edits")),
                    "friends": friends,
                    "groups": sorted({f["group"] for f in friends}),
                    "mtime": int(file_info.get("mtime_ns", 0) or 0),
                    "size": int(file_info.get("size_bytes", 0) or 0),
                    "collections": clip_cols.get(key, []),
                    "discard": bool(flags.get(key, {}).get("discard")),
                    "keep": bool(flags.get(key, {}).get("keep")),
                }
            )
        with self.lock:
            self._clips = clips
            self._collections = collections
        # map key -> absolute file for serving
        self._files = {}
        for c in clips:
            p = (self.medal_root / c["path"]).resolve()
            try:
                p.relative_to(self.medal_root)
            except ValueError:
                continue
            if p.is_file():
                self._files[c["key"]] = p

    def games(self):
        games = {}
        for c in self._clips:
            games.setdefault(c["game"], 0)
            games[c["game"]] += 1
        return [{"game": g, "clips": n} for g, n in sorted(games.items())]

    def groups(self, game: str):
        groups = {}
        for c in self._clips:
            if c["game"] != game:
                continue
            for g in c["groups"]:
                groups.setdefault(g, 0)
                groups[g] += 1
        return [{"group": g, "clips": n} for g, n in sorted(groups.items())]

    def clips(self, game: str, group: str, show_sources: bool):
        out = []
        for c in self._clips:
            if c["game"] != game or group not in c["groups"]:
                continue
            if c["keep"]:
                continue
            if (
                not show_sources
                and c["kind"] == "source"
                and c["has_edits"]
            ):
                continue
            if c["key"] not in self._files:
                continue
            out.append(c)
        out.sort(key=lambda c: c["mtime"])
        return out

    def collections(self):
        return self._collections

    def _clip(self, key: str):
        for c in self._clips:
            if c["key"] == key:
                return c
        return None

    def create_collection(self, name: str):
        name = name.strip()
        if not name:
            raise ValueError("Collection name is empty.")
        with self.lock:
            try:
                self.conn.execute(
                    "INSERT INTO triage_collections (name, created_at) VALUES (?, ?)",
                    (name, utc_now()),
                )
                self.conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError(f"Collection {name!r} already exists.")
            self._collections = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT id, name FROM triage_collections ORDER BY name COLLATE NOCASE"
                )
            ]
        return self._collections

    def set_collected(self, key: str, collection_id: int, add: bool):
        with self.lock:
            if add:
                self.conn.execute(
                    "INSERT OR IGNORE INTO triage_clip_collections VALUES (?, ?)",
                    (key, collection_id),
                )
            else:
                self.conn.execute(
                    "DELETE FROM triage_clip_collections WHERE clip_key=? AND collection_id=?",
                    (key, collection_id),
                )
            self.conn.commit()
            clip = self._clip(key)
            if clip is not None:
                if add and collection_id not in clip["collections"]:
                    clip["collections"].append(collection_id)
                elif not add and collection_id in clip["collections"]:
                    clip["collections"].remove(collection_id)

    def _set_flag(self, key: str, field: str, value: bool):
        assert field in ("discard", "keep")
        with self.lock:
            self.conn.execute(
                f"INSERT INTO triage_flags (clip_key, {field}, rated_at) VALUES (?, ?, ?) "
                f"ON CONFLICT(clip_key) DO UPDATE SET {field}=excluded.{field}, "
                "rated_at=excluded.rated_at",
                (key, 1 if value else 0, utc_now()),
            )
            self.conn.commit()
            clip = self._clip(key)
            if clip is not None:
                clip[field] = value

    def set_discard(self, key: str, discard: bool):
        self._set_flag(key, "discard", discard)

    def set_keep(self, key: str, keep: bool):
        self._set_flag(key, "keep", keep)


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Clip Triage</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#000;color:#fff;font-family:system-ui,sans-serif;overflow:hidden}
#app{height:100%;display:flex;flex-direction:column}
.screen{flex:1;overflow-y:auto;padding:16px;display:none}
.screen.active{display:block}
h2{margin-bottom:12px;font-size:20px}
.chip{display:block;width:100%;text-align:left;background:#1c1c1e;border:1px solid #333;border-radius:12px;
  color:#fff;padding:14px 16px;margin-bottom:10px;font-size:17px}
.chip small{color:#999;margin-left:8px}
#viewer{display:none;flex:1;flex-direction:column;min-height:0}
#viewer.active{display:flex}
#stage{flex:1;position:relative;min-height:0;background:#000}
#stage video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000}
#info{position:absolute;left:0;right:0;bottom:0;padding:10px 12px;
  background:linear-gradient(transparent,rgba(0,0,0,.85));font-size:13px}
#info .friends{color:#ffd60a;font-weight:600}
#info .meta{color:#aaa}
.badge{display:inline-block;background:#0a84ff;border-radius:6px;padding:2px 8px;font-size:11px;margin-right:6px}
.badge.edit{background:#30d158;color:#000}
#cols{display:flex;gap:8px;overflow-x:auto;padding:10px 12px;background:#111;flex-wrap:nowrap}
.cbtn{flex:0 0 auto;border:2px solid #444;background:#1c1c1e;color:#fff;border-radius:20px;
  padding:10px 16px;font-size:15px;min-height:48px}
.cbtn.on{border-color:#30d158;background:#30d158;color:#000;font-weight:700}
.cbtn .tick{color:#30d158;font-weight:700;margin-right:6px;visibility:hidden}
.cbtn.on .tick{visibility:visible;color:#000}
#toast{position:fixed;left:50%;bottom:120px;transform:translateX(-50%) translateY(8px);
  background:#1c1c1e;border:1px solid #555;color:#fff;padding:10px 20px;border-radius:24px;
  font-size:15px;opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;
  z-index:99;white-space:nowrap;max-width:90vw;overflow:hidden;text-overflow:ellipsis}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
#newcol{flex:0 0 auto;border:2px dashed #555;background:transparent;color:#999;border-radius:20px;
  padding:10px 16px;font-size:15px;min-height:48px}
#actions{display:flex;gap:10px;padding:10px 12px 16px;background:#111}
.abtn{flex:1;border:none;border-radius:12px;padding:16px;font-size:17px;font-weight:700;min-height:56px}
#prevb{background:#2c2c2e;color:#fff}
#discardb{background:#3a1113;color:#ff453a}
#discardb.on{background:#ff453a;color:#000}
#keepb{background:#1c2b1c;color:#30d158}
#keepb.on{background:#30d158;color:#000}
#nextb{background:#0a84ff;color:#fff}
#topbar{display:flex;align-items:center;gap:10px;padding:10px 12px;background:#111;font-size:14px}
#topbar button{background:#2c2c2e;border:none;color:#fff;border-radius:8px;padding:8px 12px;font-size:14px}
#pos{margin-left:auto;color:#999}
#empty{padding:40px 20px;text-align:center;color:#888}
</style></head><body>
<div id="app">
  <div id="games" class="screen active"><h2>Games</h2><div id="gamelist"></div></div>
  <div id="groups" class="screen"><h2 id="grouptitle">Groups</h2><div id="grouplist"></div></div>
  <div id="viewer">
    <div id="topbar">
      <button id="backb">&#8592; Groups</button>
      <span id="crumb"></span><span id="pos"></span>
      <button id="togsources" title="show raw sources with edits">src</button>
    </div>
    <div id="stage"><video id="vid" playsinline controls preload="auto" loop muted></video>
      <div id="info"></div></div>
    <div id="cols"></div>
    <div id="actions">
      <button class="abtn" id="prevb">&#8592;</button>
      <button class="abtn" id="discardb">Discard</button>
      <button class="abtn" id="keepb">Keep</button>
      <button class="abtn" id="nextb">&#8594;</button>
    </div>
  </div>
  <div id="toast"></div>
</div>
<script>
const $=id=>document.getElementById(id);
let game=null,group=null,clips=[],idx=0,collections=[],showSources=false;
async function api(p,o,retried){let r;
  try{r=await fetch(p,o);}catch(e){
    if(!retried){await new Promise(r=>setTimeout(r,600));return api(p,o,true);}
    throw new Error('server unreachable - is triage.py still running on the PC?');}
  if(!r.ok)throw new Error(await r.text());return r.json();}
function show(id){document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));
  $('viewer').classList.remove('active');
  (id==='viewer'?$('viewer'):$(id)).classList.add('active');}
async function loadGames(){const g=await api('/api/games');
  $('gamelist').innerHTML=g.length?g.map(x=>
    `<button class="chip" data-g="${esc(x.game)}">${esc(x.game)}<small>${x.clips} clips</small></button>`).join('')
    :'<div id="empty">No clips found. Run medal-sorter first.</div>';
  $('gamelist').querySelectorAll('.chip').forEach(b=>b.onclick=()=>{game=b.dataset.g;loadGroups();});}
async function loadGroups(){const g=await api('/api/groups?game='+encodeURIComponent(game));
  $('grouptitle').textContent=game;$('grouplist').innerHTML=g.map(x=>
    `<button class="chip" data-gr="${esc(x.group)}">${esc(x.group)}<small>${x.clips} clips</small></button>`).join('');
  $('grouplist').querySelectorAll('.chip').forEach(b=>b.onclick=()=>{group=b.dataset.gr;idx=0;loadClips();});
  show('groups');}
async function loadClips(){collections=await api('/api/collections');
  clips=await api('/api/clips?game='+encodeURIComponent(game)+'&group='+encodeURIComponent(group)
    +(showSources?'&show_sources=1':''));
  $('crumb').textContent=game+' / '+group;show('viewer');render();}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
let toastT=null;
function toast(msg){const t=$('toast');t.textContent=msg;t.classList.add('show');
  clearTimeout(toastT);toastT=setTimeout(()=>t.classList.remove('show'),1400);}
function render(){const c=clips[idx];const v=$('vid');
  if(!c){$('info').innerHTML='<div id="empty">No clips in this view.</div>';v.removeAttribute('src');v.load();
    $('pos').textContent='0 / 0';renderCols(null);return;}
  v.src='/video?key='+encodeURIComponent(c.key);v.play().catch(()=>{});
  $('pos').textContent=(idx+1)+' / '+clips.length;
  const fr=c.friends.map(f=>esc(f.name)).join(', ')||'no friends detected';
  const d=c.mtime?new Date(c.mtime/1e6).toLocaleDateString():'';
  $('info').innerHTML=`<span class="badge ${c.kind==='edit'?'edit':''}">${c.kind}</span>`+
    `<span class="friends">${fr}</span><div class="meta">${esc(c.game)} &middot; ${d} &middot; ${(c.size/1048576).toFixed(1)} MB</div>`;
  $('discardb').classList.toggle('on',!!c.discard);
  $('discardb').textContent=c.discard?'Marked discard':'Discard';
  $('keepb').classList.toggle('on',!!c.keep);
  $('keepb').textContent=c.keep?'Kept \u2713':'Keep';
  renderCols(c);}
function renderCols(c){$('cols').innerHTML=collections.map(x=>{
    const on=c&&c.collections.includes(x.id);
    return `<button class="cbtn${on?' on':''}" data-id="${x.id}"><span class="tick">&#10003;</span>${esc(x.name)}</button>`;}).join('')
    +'<button id="newcol">+ New</button>';
  $('cols').querySelectorAll('.cbtn').forEach(b=>b.onclick=async()=>{
    const id=+b.dataset.id,on=b.classList.contains('on');const c=clips[idx];if(!c)return;
    const name=(collections.find(x=>x.id===id)||{}).name||'collection';
    try{
      await api('/api/collect',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({key:c.key,collection_id:id,add:!on})});
      if(on)c.collections=c.collections.filter(i=>i!==id);else c.collections.push(id);
      renderCols(c);
      toast((on?'Removed from ':'Added to ')+name);
    }catch(e){toast('Error: '+e.message);}});
  $('newcol').onclick=async()=>{const n=prompt('Collection name:');if(!n)return;
    try{collections=await api('/api/collections',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:n})});renderCols(clips[idx]);}
    catch(e){alert(e.message);}};}
function step(d){if(!clips.length)return;idx=(idx+d+clips.length)%clips.length;render();}
$('nextb').onclick=()=>step(1);$('prevb').onclick=()=>step(-1);
$('discardb').onclick=async()=>{const c=clips[idx];if(!c)return;const v=!c.discard;
  try{
    await api('/api/discard',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key:c.key,discard:v})});
    c.discard=v;$('discardb').classList.toggle('on',v);
    $('discardb').textContent=v?'Marked discard':'Discard';
    toast(v?'Marked discard':'Discard cleared');
  }catch(e){toast('Error: '+e.message);}};
$('keepb').onclick=async()=>{const c=clips[idx];if(!c)return;const v=!c.keep;
  try{
    await api('/api/keep',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key:c.key,keep:v})});
    c.keep=v;
    if(v){toast('Kept');step(1);}else render();
  }catch(e){toast('Error: '+e.message);}};
$('backb').onclick=()=>loadGroups();
$('togsources').onclick=()=>{showSources=!showSources;$('togsources').style.background=showSources?'#0a84ff':'';
  idx=0;loadClips();};
document.addEventListener('keydown',e=>{if(!$('viewer').classList.contains('active'))return;
  if(e.key==='ArrowRight')step(1);else if(e.key==='ArrowLeft')step(-1);});
loadGames();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "Triage/1.0"

    @property
    def triage(self) -> Triage:
        return self.server.triage  # type: ignore

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, status, msg):
        self._json({"error": msg}, status)

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/games":
                self._json(self.triage.games())
            elif parsed.path == "/api/groups":
                self._json(self.triage.groups(q.get("game", [""])[0]))
            elif parsed.path == "/api/clips":
                self._json(
                    self.triage.clips(
                        q.get("game", [""])[0],
                        q.get("group", [""])[0],
                        show_sources=q.get("show_sources", [""])[0] == "1",
                    )
                )
            elif parsed.path == "/api/collections":
                self._json(self.triage.collections())
            elif parsed.path == "/video":
                self._serve_video(q.get("key", [""])[0])
            else:
                self._err(404, "not found")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            if parsed.path == "/api/collections":
                self._json(self.triage.create_collection(data.get("name", "")))
            elif parsed.path == "/api/collect":
                self.triage.set_collected(
                    data["key"], int(data["collection_id"]), bool(data["add"])
                )
                self._json({"ok": True})
            elif parsed.path == "/api/discard":
                self.triage.set_discard(data["key"], bool(data["discard"]))
                self._json({"ok": True})
            elif parsed.path == "/api/keep":
                self.triage.set_keep(data["key"], bool(data["keep"]))
                self._json({"ok": True})
            else:
                self._err(404, "not found")
        except (ValueError, KeyError) as e:
            self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def _serve_video(self, key: str):
        path = self.triage._files.get(key)
        if path is None:
            self._err(404, "unknown clip")
            return
        size = path.stat().st_size
        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        if range_header:
            try:
                _, spec = range_header.split("=", 1)
                s, _, e = spec.partition("-")
                start = int(s) if s else max(0, size - int(e))
                end = int(e) if e else size - 1
            except ValueError:
                self._err(416, "bad range")
                return
        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def log_message(self, fmt, *args):
        # Show API calls in the terminal for diagnostics; video chunks would spam.
        if args and "/video" not in str(args[0]):
            sys.stderr.write(
                "%s - %s\n" % (time.strftime("%H:%M:%S"), fmt % args)
            )
            sys.stderr.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="Clip triage UI (separate from medal-sorter).")
    ap.add_argument("--workspace", type=Path, default=Path.cwd(),
                    help="Directory containing clip_data.sqlite")
    ap.add_argument("--medal-root", type=Path, default=Path(r"W:\medal"))
    ap.add_argument("--host", default="0.0.0.0", help="Bind address (LAN: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    db_path = args.workspace / "clip_data.sqlite"
    if not db_path.exists():
        print(f"error: no {db_path}; run medal-sorter init/usernames first.")
        return 2
    if not args.medal_root.is_dir():
        print(f"error: medal root not found: {args.medal_root}")
        return 2

    triage = Triage(db_path, args.medal_root)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.triage = triage  # type: ignore
    print(f"Triage running: http://{args.host}:{args.port}  (workspace={args.workspace})")
    print("Reads medal-sorter tables read-only; writes only triage_* tables. No file operations.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
