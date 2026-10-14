"""Library watcher.

Polls the library on an interval instead of using inotify: it works the same
whether files arrive via Samba/NFS, a bind mount or a download client, and
has no per-directory watch limits on a very large library.

Each tick:
  * rescans the library (so new artist folders show up in Review)
  * records newly seen artist folders as "needs a genre"
  * finds defined artists whose folders changed since the last tick and, once
    their files have stopped changing for `settle_seconds` (so half-copied
    albums are never tagged), applies the genre automatically
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import core

EVENT_LIMIT = 100


class Watcher:
    def __init__(self, store: core.Store, rescan: Callable[[], List[core.ArtistEntry]],
                 start_job: Callable[..., Optional[core.Job]]):
        self.store = store
        self._rescan = rescan
        self._start_job = start_job
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self.ticking = False
        self.next_run: Optional[float] = None
        self.last_error = ""

    # ---- persisted state ---------------------------------------------------
    def _load(self) -> dict:
        st = core._read_json(self.store.dir / "watcher.json", {})
        if not isinstance(st, dict):
            st = {}
        st.setdefault("last_check", None)
        st.setdefault("known", None)       # artist folder names seen so far (None = not baselined yet)
        st.setdefault("pending", [])       # changed but still being written
        st.setdefault("needs_apply", [])   # auto-apply is off: new files waiting for a manual apply
        st.setdefault("events", [])
        return st

    def _save(self, st: dict) -> None:
        core._write_json_atomic(self.store.dir / "watcher.json", st)

    def _event(self, st: dict, kind: str, artist: str = "", detail: str = "") -> None:
        st["events"].insert(0, {"t": time.time(), "kind": kind, "artist": artist, "detail": detail})
        del st["events"][EVENT_LIMIT:]

    def status(self) -> dict:
        st = self._load()
        cfg = self.store.load_config()
        return {
            "enabled": cfg["watcher_enabled"], "interval_min": cfg["watch_interval_min"],
            "settle_seconds": cfg["settle_seconds"], "auto_apply": cfg["auto_apply"],
            "ticking": self.ticking, "last_check": st["last_check"], "next_check": self.next_run,
            "pending": len(st["pending"]), "needs_apply": st["needs_apply"],
            "events": st["events"][:30], "error": self.last_error,
        }

    def clear_needs_apply(self) -> None:
        with self._lock:
            st = self._load()
            if st["needs_apply"]:
                st["needs_apply"] = []
                self._save(st)

    # ---- control -----------------------------------------------------------
    def run_now(self) -> None:
        self._wake.set()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="watcher").start()

    def _loop(self) -> None:
        time.sleep(15)  # let the app settle after startup
        while True:
            cfg = self.store.load_config()
            if cfg["watcher_enabled"] or self._wake.is_set():
                try:
                    self.tick()
                    self.last_error = ""
                except Exception as e:  # never let the thread die
                    self.last_error = f"{e.__class__.__name__}: {e}"
            wait = max(60, cfg["watch_interval_min"] * 60)
            self.next_run = time.time() + wait if cfg["watcher_enabled"] else None
            self._wake.wait(wait)
            self._wake.clear()

    # ---- one pass ----------------------------------------------------------
    def tick(self) -> None:
        with self._lock:
            self.ticking = True
            try:
                self._tick()
            finally:
                self.ticking = False

    def _tick(self) -> None:
        cfg = self.store.load_config()
        if not Path(cfg["library_base"]).is_dir():
            return
        started = time.time()
        artists = {a.name: a for a in self._rescan()}
        defs = self.store.load_defs()
        st = self._load()

        # first run: remember what's already there rather than announcing all of it
        if st["known"] is None:
            st["known"] = sorted(artists)
            st["last_check"] = self.store.last_apply() or started
            self._save(st)
            return

        known = set(st["known"])
        candidates = set(st["pending"])
        for name in sorted(set(artists) - known):
            if defs.get(name):
                candidates.add(name)
                self._event(st, "new_defined", name, "new folder for an artist you've already defined")
            else:
                self._event(st, "new_artist", name, "needs a genre, see Review")
        st["known"] = sorted(known | set(artists))

        since = st["last_check"] or started
        for name, a in artists.items():
            if defs.get(name) and name not in candidates and core.has_changes_since(a.path, since):
                candidates.add(name)
        candidates = {n for n in candidates if n in artists and defs.get(n)}

        # wait until files stop changing, so half-copied albums aren't tagged
        ready, pending = [], []
        for name in sorted(candidates):
            if started - core.newest_mtime(artists[name].path) >= cfg["settle_seconds"]:
                ready.append(name)
            else:
                pending.append(name)

        if ready:
            if cfg["auto_apply"]:
                job = self._start_job("auto", [(n, defs[n], artists[n].path) for n in ready],
                                      None, cfg["dry_run"], "Watcher: new or changed files")
                if job is None:          # a manual apply is running; try again next tick
                    pending += ready
                else:
                    while job.state == "running":
                        time.sleep(1)
                    done = {e["artist"]: e for e in job.log}
                    for n in ready:
                        e = done.get(n)
                        if job.state != "done" or e is None:
                            self._event(st, "apply_failed", n, job.message or job.state)
                        elif e["errors"]:
                            self._event(st, "apply_errors", n, f"{e['errors']} file(s) failed")
                        elif e["changed"]:
                            self._event(st, "applied", n, f"{e['genre']}: {e['changed']} of {e['files']} file(s) tagged"
                                        + (" (dry run)" if job.dry_run else ""))
            else:
                st["needs_apply"] = sorted(set(st["needs_apply"]) | set(ready))
                for n in ready:
                    self._event(st, "needs_apply", n, "new files; auto-apply is off")

        st["pending"] = sorted(set(pending))
        st["last_check"] = started - 1
        self._save(st)
