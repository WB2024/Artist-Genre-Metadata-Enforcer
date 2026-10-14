"""Core logic: library discovery, definitions storage, tag writing.

Ported from the original TUI script (music-genre-enforcer.py) with the
interactive parts removed.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from mutagen import File as MutagenFile

SUPPORTED_EXTS_DEFAULT = [
    ".aif", ".aiff", ".ape", ".asf", ".dff", ".dsf", ".flac", ".m4a", ".mp3",
    ".mp4", ".oga", ".ogg", ".opus", ".wma", ".wv",
]
IGNORE_EXTS = {".lrc", ".jpg", ".jpeg", ".png", ".gif", ".txt", ".nfo", ".cue", ".log", ".pdf"}

# Defaults: top-level folders skipped entirely, and "flat" folders that hold
# albums directly (base/<group>/<album>/...) and are treated as ONE artist.
EXCLUDED_FOLDERS_DEFAULT = ["Playlists"]
FLAT_FOLDERS_DEFAULT = ["Various Artists"]

PEEK_SAMPLE_SIZE = 25
HISTORY_LIMIT = 30


def validate_single_genre(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return "Genre cannot be empty."
    if any(sep in s for sep in (";", "|", "/")):
        return "Single genre only (no ';', '|', or '/')."
    return None


def normalize_names(names: Iterable[str], default: Optional[List[str]] = None) -> List[str]:
    out, seen = [], set()
    for n in (names if names is not None else (default or [])):
        n = str(n).strip().strip("/")
        if n and n.casefold() not in seen:
            seen.add(n.casefold())
            out.append(n)
    return out


def _clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def normalize_exts(exts: Iterable[str]) -> List[str]:
    out = set()
    for e in exts:
        e = str(e).strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        out.add(e)
    return sorted(out)


# --------------------------------------------------------------------------
# Persistent storage
# --------------------------------------------------------------------------

def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class Store:
    """config.json / artist_genres.json / last_apply.json / history.json in one dir."""

    def __init__(self, cfg_dir: Path, default_library: str):
        self.dir = cfg_dir
        self.default_library = default_library
        self.lock = threading.RLock()

    # config ---------------------------------------------------------------
    def load_config(self) -> dict:
        raw = _read_json(self.dir / "config.json", {})
        if not isinstance(raw, dict):
            raw = {}
        return {
            "library_base": str(raw.get("library_base") or self.default_library),
            "supported_exts": normalize_exts(raw.get("supported_exts") or SUPPORTED_EXTS_DEFAULT),
            "dry_run": bool(raw.get("dry_run", False)),
            "workers": max(1, min(32, int(raw.get("workers", 8) or 8))),
            "excluded_folders": normalize_names(raw.get("excluded_folders"), EXCLUDED_FOLDERS_DEFAULT),
            "flat_folders": normalize_names(raw.get("flat_folders"), FLAT_FOLDERS_DEFAULT),
            "lastfm_api_key": str(raw.get("lastfm_api_key") or "").strip(),
            "watcher_enabled": bool(raw.get("watcher_enabled", True)),
            "watch_interval_min": _clamp(raw.get("watch_interval_min"), 1, 1440, 10),
            "settle_seconds": _clamp(raw.get("settle_seconds"), 0, 3600, 120),
            "auto_apply": bool(raw.get("auto_apply", True)),
        }

    def save_config(self, cfg: dict) -> dict:
        with self.lock:
            clean = {
                "library_base": str(cfg["library_base"]).strip(),
                "supported_exts": normalize_exts(cfg["supported_exts"]),
                "dry_run": bool(cfg["dry_run"]),
                "workers": max(1, min(32, int(cfg.get("workers", 8)))),
                "excluded_folders": normalize_names(cfg.get("excluded_folders", [])),
                "flat_folders": normalize_names(cfg.get("flat_folders", [])),
                "lastfm_api_key": str(cfg.get("lastfm_api_key") or "").strip(),
                "watcher_enabled": bool(cfg.get("watcher_enabled", True)),
                "watch_interval_min": _clamp(cfg.get("watch_interval_min"), 1, 1440, 10),
                "settle_seconds": _clamp(cfg.get("settle_seconds"), 0, 3600, 120),
                "auto_apply": bool(cfg.get("auto_apply", True)),
            }
            _write_json_atomic(self.dir / "config.json", clean)
            return clean

    # definitions ----------------------------------------------------------
    def load_defs(self) -> Dict[str, str]:
        raw = _read_json(self.dir / "artist_genres.json", {})
        return parse_defs(raw)

    def save_defs(self, defs: Dict[str, str]) -> None:
        with self.lock:
            _write_json_atomic(self.dir / "artist_genres.json", dict(sorted(defs.items(), key=lambda kv: kv[0].casefold())))

    def backup_defs(self) -> Optional[str]:
        src = self.dir / "artist_genres.json"
        if not src.exists():
            return None
        dst = self.dir / "backups" / f"artist_genres.{time.strftime('%Y%m%d-%H%M%S')}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        # keep the 20 most recent
        olds = sorted(dst.parent.glob("artist_genres.*.json"))
        for p in olds[:-20]:
            p.unlink(missing_ok=True)
        return dst.name

    # last apply / history -------------------------------------------------
    def last_apply(self) -> Optional[float]:
        raw = _read_json(self.dir / "last_apply.json", None)
        if isinstance(raw, dict) and "timestamp" in raw:
            try:
                return float(raw["timestamp"])
            except (TypeError, ValueError):
                return None
        return None

    def save_last_apply(self, ts: float) -> None:
        _write_json_atomic(self.dir / "last_apply.json", {"timestamp": ts})

    def history(self) -> List[dict]:
        raw = _read_json(self.dir / "history.json", [])
        return raw if isinstance(raw, list) else []

    def add_history(self, entry: dict) -> None:
        with self.lock:
            h = self.history()
            h.insert(0, entry)
            _write_json_atomic(self.dir / "history.json", h[:HISTORY_LIMIT])


def parse_defs(raw) -> Dict[str, str]:
    """Accept {artist: genre}; ignore junk. Empty genre = undefined."""
    if not isinstance(raw, dict):
        raise ValueError("JSON must be an object mapping artist name to genre.")
    out: Dict[str, str] = {}
    for k, v in raw.items():
        k = str(k).strip()
        v = "" if v is None else str(v).strip()
        if k:
            out[k] = v
    return out


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------

@dataclass
class ArtistEntry:
    name: str
    group: str
    path: str


def discover_artists(base: Path, excluded: Iterable[str] = (), flat: Iterable[str] = ()) -> List[ArtistEntry]:
    excluded = {n.casefold() for n in excluded}
    flat = {n.casefold() for n in flat}
    artists: List[ArtistEntry] = []
    if not base.is_dir():
        return artists
    with os.scandir(base) as it:
        groups = sorted((e for e in it if e.is_dir() and e.name.casefold() not in excluded),
                        key=lambda e: e.name.casefold())
    for g in groups:
        if g.name.casefold() in flat:
            artists.append(ArtistEntry(g.name, g.name, g.path))
            continue
        try:
            with os.scandir(g.path) as it:
                subs = sorted((e for e in it if e.is_dir()), key=lambda e: e.name.casefold())
        except OSError:
            continue
        for a in subs:
            artists.append(ArtistEntry(a.name, g.name, a.path))
    return artists


def iter_audio_files(root: str, exts: Set[str]) -> Iterable[str]:
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in IGNORE_EXTS:
                continue
            if ext in exts:
                yield os.path.join(dirpath, fn)


def has_changes_since(root: str, since: float) -> bool:
    try:
        if os.stat(root).st_mtime > since:
            return True
    except OSError:
        return True
    for dirpath, dirnames, _ in os.walk(root):
        for d in dirnames:
            try:
                if os.path.getmtime(os.path.join(dirpath, d)) > since:
                    return True
            except OSError:
                return True
    return False


def newest_mtime(root: str) -> float:
    """Newest mtime of any file or directory under root (0 if unreadable)."""
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            try:
                newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
            except OSError:
                pass
    try:
        newest = max(newest, os.stat(root).st_mtime)
    except OSError:
        pass
    return newest


def _first_genre(audio) -> Optional[str]:
    if audio is None or audio.tags is None:
        return None
    g = audio.tags.get("genre")
    if isinstance(g, list):
        return g[0] if g else None
    return g if isinstance(g, str) else None


def _all_genres(audio) -> list:
    """Every GENRE value on the file (multi-valued tags included), stripped."""
    if audio is None or audio.tags is None:
        return []
    g = audio.tags.get("genre")
    if g is None:
        return []
    if isinstance(g, str):
        g = [g]
    return [str(x).strip() for x in g if str(x).strip()]


def peek_tags(artist_path: str, exts: Set[str], sample: int = PEEK_SAMPLE_SIZE) -> dict:
    files = list(iter_audio_files(artist_path, exts))
    counts: Dict[str, int] = {}
    for f in files[:sample]:
        try:
            audio = MutagenFile(f, easy=True)
            if audio is None or audio.tags is None:
                key = "(no tags)"
            else:
                v = "; ".join(_all_genres(audio))
                key = v if v else "(none)"
        except Exception:
            key = "(error)"
        counts[key] = counts.get(key, 0) + 1
    return {
        "total": len(files),
        "sampled": min(sample, len(files)),
        "genres": [{"genre": g, "count": c} for g, c in sorted(counts.items(), key=lambda kv: -kv[1])],
    }


def _retouch_later(paths, delay: float = 90.0) -> None:
    """Bump mtime of rewritten files shortly after the write.

    Navidrome may read a file while it is being rewritten and store stale tags
    together with the new mtime, so quick scans never re-read it. A later mtime
    guarantees its watcher sees a change and rescans.
    """
    paths = list(paths)
    if not paths:
        return

    def _run():
        for p in paths:
            try:
                os.utime(p)
            except OSError:
                pass

    t = threading.Timer(delay, _run)
    t.daemon = True
    t.start()


def set_genre_on_file(path: str, genre: str, dry_run: bool) -> Tuple[bool, str]:
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return (False, "unsupported")
        if audio.tags is None:
            try:
                audio.add_tags()
            except Exception:
                pass
        if audio.tags is None:
            return (False, "no-tags")
        # Only skip if the tag is EXACTLY [genre]: extra values must be overwritten.
        if _all_genres(audio) == [genre.strip()]:
            return (False, "ok-same")
        audio.tags["genre"] = [genre]
        if not dry_run:
            audio.save()
        return (True, "ok-set")
    except Exception as e:
        return (False, f"error:{e.__class__.__name__}")


# --------------------------------------------------------------------------
# Apply job
# --------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    mode: str
    dry_run: bool
    started: float
    state: str = "running"        # running | done | cancelled | error
    phase: str = "starting"       # checking | applying
    total_artists: int = 0
    done_artists: int = 0
    current: str = ""
    totals: Dict[str, int] = field(default_factory=lambda: {"files": 0, "changed": 0, "skipped": 0, "errors": 0})
    log: List[dict] = field(default_factory=list)
    finished: Optional[float] = None
    message: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)

    def to_dict(self, log_tail: int = 400) -> dict:
        return {
            "id": self.id, "mode": self.mode, "dry_run": self.dry_run,
            "started": self.started, "finished": self.finished,
            "state": self.state, "phase": self.phase, "message": self.message,
            "total_artists": self.total_artists, "done_artists": self.done_artists,
            "current": self.current, "totals": self.totals,
            "log": self.log[-log_tail:],
        }


def run_apply(job: Job, store: Store, cfg: dict, targets: List[Tuple[str, str, str]],
              only_modified_since: Optional[float], on_change: Callable[[], None]) -> None:
    """targets: [(artist, genre, path)]. Mutates job; runs in a worker thread."""
    try:
        exts = set(cfg["supported_exts"])
        workers = cfg["workers"]

        if only_modified_since is not None:
            job.phase = "checking"
            kept = []
            for artist, genre, path in targets:
                if job.cancel.is_set():
                    break
                job.current = artist
                if has_changes_since(path, only_modified_since):
                    kept.append((artist, genre, path))
            targets = kept

        job.phase = "applying"
        job.total_artists = len(targets)
        job.current = ""
        if not targets and not job.cancel.is_set():
            job.message = "Nothing to do: no artists modified since the last apply."

        for artist, genre, path in targets:
            if job.cancel.is_set():
                break
            job.current = artist
            files = list(iter_audio_files(path, exts))
            c = {"files": len(files), "changed": 0, "skipped": 0, "errors": 0}
            changed_files: List[str] = []
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for f, (ok, status) in zip(files, ex.map(lambda f: set_genre_on_file(f, genre, job.dry_run), files)):
                    if status.startswith("error:"):
                        c["errors"] += 1
                    elif ok:
                        c["changed"] += 1
                        if not job.dry_run:
                            changed_files.append(f)
                    else:
                        c["skipped"] += 1
                    if job.cancel.is_set():
                        break
            _retouch_later(changed_files)
            for k, v in c.items():
                job.totals[k] += v
            job.done_artists += 1
            job.log.append({"artist": artist, "genre": genre, **c})

        job.current = ""
        if job.cancel.is_set():
            job.state = "cancelled"
        else:
            job.state = "done"
            # Auto (watcher) runs only touch some artists, so they must not move the
            # smart-apply timestamp.
            if not job.dry_run and job.mode != "auto":
                store.save_last_apply(job.started)
    except Exception as e:  # pragma: no cover
        job.state = "error"
        job.message = f"{e.__class__.__name__}: {e}"
    finally:
        job.finished = time.time()
        quiet = job.mode == "auto" and job.state == "done" and not job.totals["changed"] and not job.totals["errors"]
        if not quiet:  # don't fill the history with no-op watcher runs
            store.add_history({k: v for k, v in job.to_dict(log_tail=0).items() if k != "log"})
        on_change()


# --------------------------------------------------------------------------
# Last.fm
# --------------------------------------------------------------------------

class LastFmError(Exception):
    pass


def lastfm_top_tags(api_key: str, artist: str, limit: int = 12) -> List[dict]:
    """Return [{name, count}] from artist.getTopTags (count is 0-100 relative weight)."""
    import urllib.error
    import urllib.parse
    import urllib.request

    qs = urllib.parse.urlencode({
        "method": "artist.gettoptags", "artist": artist, "api_key": api_key,
        "autocorrect": 1, "format": "json",
    })
    req = urllib.request.Request("https://ws.audioscrobbler.com/2.0/?" + qs,
                                 headers={"User-Agent": "music-genre-enforcer/1.2"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            raise LastFmError(f"Last.fm returned HTTP {e.code}")
    except Exception as e:
        raise LastFmError(f"Couldn't reach Last.fm ({e.__class__.__name__})")
    if "error" in data:
        code = data.get("error")
        if code in (10, 26):
            raise LastFmError("Last.fm rejected the API key. Check it in Settings.")
        if code == 6:
            return []
        raise LastFmError(data.get("message") or f"Last.fm error {code}")
    tags = (data.get("toptags") or {}).get("tag") or []
    if isinstance(tags, dict):
        tags = [tags]
    out = []
    for t in tags:
        try:
            out.append({"name": str(t["name"]).strip(), "count": int(t.get("count") or 0)})
        except (KeyError, ValueError, TypeError):
            continue
    return [t for t in out if t["name"] and t["count"] >= 5][:limit]
