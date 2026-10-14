"""FastAPI backend for Music Genre Enforcer."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
DEFAULT_LIBRARY = os.environ.get("LIBRARY_PATH", "/music")
STATIC_DIR = Path(__file__).parent / "static"

store = core.Store(CONFIG_DIR, DEFAULT_LIBRARY)
app = FastAPI(title="Music Genre Enforcer", docs_url="/api/docs", openapi_url="/api/openapi.json")

_scan_lock = threading.Lock()
_scan: dict = {"artists": [], "at": None, "base": None}
_job: Optional[core.Job] = None
_job_lock = threading.Lock()


# ---------------------------------------------------------------- helpers

def _rescan() -> None:
    cfg = store.load_config()
    with _scan_lock:
        _scan["artists"] = core.discover_artists(Path(cfg["library_base"]), cfg["excluded_folders"], cfg["flat_folders"])
        _scan["at"] = time.time()
        _scan["base"] = cfg["library_base"]


def _artists() -> List[core.ArtistEntry]:
    if _scan["at"] is None or _scan["base"] != store.load_config()["library_base"]:
        _rescan()
    return _scan["artists"]


def _by_name() -> Dict[str, core.ArtistEntry]:
    return {a.name: a for a in _artists()}


def _genre_counts(defs: Dict[str, str]) -> List[dict]:
    counts: Dict[str, int] = {}
    for g in defs.values():
        if g:
            counts[g] = counts.get(g, 0) + 1
    return [{"genre": g, "count": c} for g, c in sorted(counts.items(), key=lambda kv: kv[0].casefold())]


def _public_cfg(cfg: dict) -> dict:
    """Config for the browser: never send the Last.fm key back."""
    out = {k: v for k, v in cfg.items() if k != "lastfm_api_key"}
    out["lastfm_configured"] = bool(cfg.get("lastfm_api_key"))
    return out


def _clean_genre(g: str) -> str:
    err = core.validate_single_genre(g)
    if err:
        raise HTTPException(422, err)
    return g.strip()


# ---------------------------------------------------------------- state

@app.on_event("startup")
def _startup() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_rescan, daemon=True).start()


@app.get("/api/state")
def state():
    cfg = store.load_config()
    defs = store.load_defs()
    artists = _artists()
    names = {a.name for a in artists}
    defined_on_disk = sum(1 for n in names if defs.get(n))
    return {
        "config": _public_cfg(cfg),
        "library_exists": Path(cfg["library_base"]).is_dir(),
        "scanned_at": _scan["at"],
        "counts": {
            "artists": len(artists),
            "defined": defined_on_disk,
            "undefined": len(artists) - defined_on_disk,
            "orphans": sum(1 for k, v in defs.items() if v and k not in names),
            "genres": len(_genre_counts(defs)),
        },
        "last_apply": store.last_apply(),
        "job": _job.to_dict() if _job else None,
    }


class ConfigIn(BaseModel):
    library_base: str
    supported_exts: List[str]
    dry_run: bool
    workers: int = 8
    excluded_folders: List[str] = []
    flat_folders: List[str] = []
    lastfm_api_key: Optional[str] = None   # None = keep existing, "" = clear


@app.put("/api/config")
def put_config(body: ConfigIn):
    if not body.library_base.strip():
        raise HTTPException(422, "Library path cannot be empty.")
    data = body.model_dump()
    if data["lastfm_api_key"] is None:
        data["lastfm_api_key"] = store.load_config()["lastfm_api_key"]
    cfg = store.save_config(data)
    _lastfm_cache.clear()
    _rescan()
    return {"config": _public_cfg(cfg), "library_exists": Path(cfg["library_base"]).is_dir()}


@app.post("/api/scan")
def scan():
    _rescan()
    return {"artists": len(_scan["artists"]), "scanned_at": _scan["at"]}


# ---------------------------------------------------------------- artists

@app.get("/api/artists")
def list_artists():
    defs = store.load_defs()
    out = []
    seen = set()
    for a in _artists():
        seen.add(a.name)
        out.append({"name": a.name, "group": a.group, "genre": defs.get(a.name, ""), "on_disk": True})
    for k, v in defs.items():
        if k not in seen and v:
            out.append({"name": k, "group": "", "genre": v, "on_disk": False})
    out.sort(key=lambda x: x["name"].casefold())
    return {"artists": out, "genres": _genre_counts(defs)}


class GenreIn(BaseModel):
    name: str
    genre: str


@app.put("/api/artist")
def set_artist(body: GenreIn):
    genre = _clean_genre(body.genre)
    with store.lock:
        defs = store.load_defs()
        defs[body.name] = genre
        store.save_defs(defs)
    return {"ok": True, "genres": _genre_counts(defs)}


@app.delete("/api/artist")
def delete_artist(name: str):
    with store.lock:
        defs = store.load_defs()
        if name in defs:
            del defs[name]
            store.save_defs(defs)
    return {"ok": True, "genres": _genre_counts(defs)}


@app.get("/api/artist/tags")
def artist_tags(name: str):
    a = _by_name().get(name)
    if not a:
        raise HTTPException(404, "Artist folder not found on disk.")
    cfg = store.load_config()
    return core.peek_tags(a.path, set(cfg["supported_exts"]))


# ---------------------------------------------------------------- last.fm

_lastfm_cache: Dict[str, list] = {}


@app.get("/api/lastfm")
def lastfm(name: str):
    key = store.load_config()["lastfm_api_key"]
    if not key:
        raise HTTPException(400, "No Last.fm API key set. Add one in Settings.")
    ck = name.casefold()
    if ck not in _lastfm_cache:
        try:
            _lastfm_cache[ck] = core.lastfm_top_tags(key, name)
        except core.LastFmError as e:
            raise HTTPException(502, str(e))
    return {"tags": _lastfm_cache[ck]}


# ---------------------------------------------------------------- genres

class RenameIn(BaseModel):
    old: str
    new: str


@app.post("/api/genres/rename")
def rename_genre(body: RenameIn):
    new = _clean_genre(body.new)
    with store.lock:
        defs = store.load_defs()
        hits = [k for k, v in defs.items() if v == body.old]
        if not hits:
            raise HTTPException(404, "No artists use that genre.")
        store.backup_defs()
        for k in hits:
            defs[k] = new
        store.save_defs(defs)
    return {"updated": len(hits), "genres": _genre_counts(defs)}


# ---------------------------------------------------------------- import / export

@app.get("/api/export")
def export():
    defs = store.load_defs()
    body = json.dumps(dict(sorted(defs.items(), key=lambda kv: kv[0].casefold())), indent=2, ensure_ascii=False) + "\n"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="artist_genres.json"'})


@app.post("/api/import")
async def import_defs(file: UploadFile = File(...), mode: str = Form("merge"), commit: bool = Form(False)):
    """
    mode:
      merge    - imported values win on conflict; keep everything else
      fill     - only add artists not already defined; never overwrite
      replace  - discard current definitions, use the file as-is
    commit=false returns the preview only.
    """
    if mode not in ("merge", "fill", "replace"):
        raise HTTPException(422, "Unknown mode.")
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "File too large.")
    try:
        incoming = core.parse_defs(json.loads(raw.decode("utf-8-sig")))
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(422, f"Invalid JSON: {e}")

    invalid = [k for k, v in incoming.items() if v and core.validate_single_genre(v)]
    incoming = {k: v for k, v in incoming.items() if k not in invalid}

    with store.lock:
        current = store.load_defs()
        cur_set = {k: v for k, v in current.items() if v}
        added = {k: v for k, v in incoming.items() if v and k not in cur_set}
        changed = {k: {"from": cur_set[k], "to": v} for k, v in incoming.items() if v and k in cur_set and cur_set[k] != v}
        same = sum(1 for k, v in incoming.items() if v and cur_set.get(k) == v)
        removed = [k for k in cur_set if k not in incoming] if mode == "replace" else []

        if mode == "merge":
            result = {**current, **{k: v for k, v in incoming.items() if v}}
        elif mode == "fill":
            result = {**current, **added}
        else:
            result = dict(incoming)

        if commit:
            backup = store.backup_defs()
            store.save_defs(result)
        else:
            backup = None

    names = set(_by_name())
    return {
        "committed": commit, "backup": backup, "mode": mode,
        "added": len(added), "changed_count": len(changed) if mode != "fill" else 0,
        "skipped_conflicts": len(changed) if mode == "fill" else 0,
        "unchanged": same, "removed": len(removed), "invalid": invalid[:50],
        "not_on_disk": sum(1 for k in incoming if k not in names),
        "changed": [{"name": k, **v} for k, v in list(changed.items())[:200]],
        "added_sample": [{"name": k, "genre": v} for k, v in list(added.items())[:200]],
        "total_after": len([1 for v in result.values() if v]),
    }


# ---------------------------------------------------------------- apply

class ApplyIn(BaseModel):
    mode: str = "smart"            # smart | full | selected
    dry_run: Optional[bool] = None  # None = use config
    artists: Optional[List[str]] = None


@app.post("/api/apply")
def apply(body: ApplyIn):
    global _job
    with _job_lock:
        if _job and _job.state == "running":
            raise HTTPException(409, "A job is already running.")
        cfg = store.load_config()
        if not Path(cfg["library_base"]).is_dir():
            raise HTTPException(400, f"Library path does not exist: {cfg['library_base']}")
        defs = store.load_defs()
        by_name = _by_name()
        if body.mode == "selected":
            wanted = set(body.artists or [])
            pool = [n for n in wanted]
        else:
            pool = list(defs)
        targets = sorted(((n, defs[n], by_name[n].path) for n in pool if defs.get(n) and n in by_name),
                         key=lambda t: t[0].casefold())
        if not targets:
            raise HTTPException(400, "No defined artists found on disk to apply.")

        since = None
        if body.mode == "smart":
            since = store.last_apply()  # None -> first run, process everything
        dry = cfg["dry_run"] if body.dry_run is None else body.dry_run
        job = core.Job(id=uuid.uuid4().hex[:8], mode=body.mode, dry_run=dry, started=time.time())
        if body.mode == "smart" and since is None:
            job.message = "No previous apply found, processing all artists."
        _job = job
        threading.Thread(target=core.run_apply, args=(job, store, cfg, targets, since, lambda: None), daemon=True).start()
        return job.to_dict()


@app.get("/api/job")
def get_job():
    return _job.to_dict() if _job else None


@app.post("/api/job/cancel")
def cancel_job():
    if _job and _job.state == "running":
        _job.cancel.set()
    return {"ok": True}


@app.get("/api/history")
def history():
    return store.history()


# ---------------------------------------------------------------- frontend

@app.middleware("http")
async def _no_stale_assets(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/assets/"):
        resp.headers["Cache-Control"] = "no-cache"  # always revalidate (ETag) so deploys show up
    return resp


app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
