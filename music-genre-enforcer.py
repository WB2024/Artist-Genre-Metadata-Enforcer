#!/usr/bin/env python3
"""
Music Genre Enforcer (Debian-friendly, stdlib UI, persistent data, filesystem-driven)

Install dependency via apt:
  apt install -y python3-mutagen

What it does:
- Reads your library structure from filesystem:
    BASE/<group>/<artist>/...
- Prompts you once per *artist folder* for a single genre string.
- Saves artist->genre mapping, so next run only asks for new artists.
- Writes the GENRE tag to all supported audio files under each artist folder (mutagen).

Config:
  ~/.config/music-genre-enforcer/config.json
Definitions:
  ~/.config/music-genre-enforcer/artist_genres.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    from mutagen import File as MutagenFile
except ImportError:
    print("Missing dependency: mutagen", file=sys.stderr)
    print("Install on Debian/Ubuntu: apt install -y python3-mutagen", file=sys.stderr)
    raise

APP_DIR_NAME = "music-genre-enforcer"
CONFIG_FILE_NAME = "config.json"
DEFINITIONS_FILE_NAME = "artist_genres.json"
LAST_APPLY_FILE_NAME = "last_apply.json"

SUPPORTED_EXTS_DEFAULT = {
    ".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".oga",
    ".aiff", ".aif", ".ape", ".wv", ".asf", ".wma", ".dsf", ".dff",
}
IGNORE_EXTS = {".lrc", ".jpg", ".jpeg", ".png", ".gif", ".txt", ".nfo", ".cue", ".log", ".pdf"}

BANNER = "=" * 78


@dataclass
class AppConfig:
    library_base: Path
    supported_exts: Set[str]
    dry_run: bool = False


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_exts(exts: Iterable[str]) -> Set[str]:
    out = set()
    for e in exts:
        e = str(e).strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        out.add(e)
    return out


def load_config(cfg_dir: Path) -> Optional[AppConfig]:
    raw = read_json(cfg_dir / CONFIG_FILE_NAME, None)
    if not isinstance(raw, dict):
        return None
    base = raw.get("library_base")
    if not base:
        return None
    exts = normalize_exts(raw.get("supported_exts", sorted(SUPPORTED_EXTS_DEFAULT)))
    dry = bool(raw.get("dry_run", False))
    return AppConfig(library_base=Path(base), supported_exts=exts, dry_run=dry)


def save_config(cfg_dir: Path, cfg: AppConfig) -> None:
    write_json(cfg_dir / CONFIG_FILE_NAME, {
        "library_base": str(cfg.library_base),
        "supported_exts": sorted(cfg.supported_exts),
        "dry_run": cfg.dry_run,
    })


def load_definitions(cfg_dir: Path) -> Dict[str, str]:
    raw = read_json(cfg_dir / DEFINITIONS_FILE_NAME, {})
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        k = str(k).strip()
        v = "" if v is None else str(v).strip()
        if k:
            out[k] = v
    return out


def save_definitions(cfg_dir: Path, defs: Dict[str, str]) -> None:
    write_json(cfg_dir / DEFINITIONS_FILE_NAME, defs)


def load_last_apply_time(cfg_dir: Path) -> Optional[float]:
    """Load the timestamp of the last successful apply run."""
    raw = read_json(cfg_dir / LAST_APPLY_FILE_NAME, None)
    if isinstance(raw, dict) and "timestamp" in raw:
        return float(raw["timestamp"])
    return None


def save_last_apply_time(cfg_dir: Path, timestamp: float) -> None:
    """Save the timestamp of a successful apply run."""
    write_json(cfg_dir / LAST_APPLY_FILE_NAME, {"timestamp": timestamp})


def has_changes_since(root: Path, since: float) -> bool:
    """
    Check if any directory under root has mtime > since.
    This catches new albums (new directories) and new files (updates parent dir mtime).
    Short-circuits on first match for speed.
    """
    try:
        if root.stat().st_mtime > since:
            return True
    except OSError:
        return True  # If we can't stat, assume changed
    
    for dirpath, dirnames, _ in os.walk(root):
        for dirname in dirnames:
            try:
                if os.path.getmtime(os.path.join(dirpath, dirname)) > since:
                    return True
            except OSError:
                return True  # If we can't stat, assume changed
    return False


def pause(msg: str = "Press Enter to continue...") -> None:
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        print()


def clear() -> None:
    os.system("clear" if os.name != "nt" else "cls")


def prompt(msg: str, default: str = "") -> str:
    if default:
        s = input(f"{msg} [{default}]: ").strip()
        return s if s else default
    return input(f"{msg}: ").strip()


def prompt_yes_no(msg: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    s = input(f"{msg} [{suffix}]: ").strip().lower()
    if not s:
        return default
    return s in ("y", "yes", "1", "true")


def validate_single_genre(s: str) -> Optional[str]:
    s = s.strip()
    if not s:
        return "Genre cannot be empty."
    # enforce "single genre only"
    if any(sep in s for sep in [";", "|", "/"]):
        return "Single genre only (no ';', '|', or '/')."
    return None


def discover_artist_dirs(base: Path) -> List[Path]:
    """
    Your structure:
      base/<group>/<artist>/...

    Returns list of artist dir Paths.
    Uses os.scandir() for faster directory enumeration.
    """
    artists: List[Path] = []
    if not base.exists():
        return artists

    with os.scandir(base) as groups:
        sorted_groups = sorted([e for e in groups if e.is_dir()], key=lambda e: e.name.casefold())
    for group in sorted_groups:
        with os.scandir(group.path) as artist_entries:
            sorted_artists = sorted([e for e in artist_entries if e.is_dir()], key=lambda e: e.name.casefold())
        for artist in sorted_artists:
            artists.append(Path(artist.path))
    return artists


def is_audio_file(path: Path, supported_exts: Set[str]) -> bool:
    ext = path.suffix.lower()
    if ext in IGNORE_EXTS:
        return False
    return ext in supported_exts


def iter_audio_files(root: Path, supported_exts: Set[str]) -> Iterable[Path]:
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            # Early extension check before creating Path object
            ext = os.path.splitext(fn)[1].lower()
            if ext in IGNORE_EXTS:
                continue
            if ext in supported_exts:
                yield Path(dirpath) / fn


def set_genre_on_file(path: Path, genre: str, dry_run: bool) -> Tuple[bool, str]:
    """
    Returns: (changed, status_message)
    """
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return (False, "unsupported")
        if audio.tags is None:
            try:
                audio.add_tags()
            except Exception:
                # some formats may not support add_tags; sometimes tags appear after save attempts
                pass
        if audio.tags is None:
            return (False, "no-tags")

        existing = audio.tags.get("genre")
        existing_val = existing[0] if isinstance(existing, list) and existing else (existing if isinstance(existing, str) else None)
        if existing_val == genre:
            return (False, "ok-same")

        audio.tags["genre"] = [genre]
        if not dry_run:
            audio.save()
        return (True, "ok-set")
    except Exception as e:
        return (False, f"error:{e.__class__.__name__}")


def apply_genre_to_artist_dir(artist_dir: Path, genre: str, cfg: AppConfig, workers: int = 8) -> Dict[str, int]:
    """Apply genre to all audio files in artist_dir using parallel processing."""
    files = list(iter_audio_files(artist_dir, cfg.supported_exts))
    total = len(files)
    changed = skipped = errors = 0

    if not files:
        return {"total": 0, "changed": 0, "skipped": 0, "errors": 0}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(set_genre_on_file, f, genre, cfg.dry_run): f for f in files}
        for future in as_completed(futures):
            ok, status = future.result()
            if status.startswith("error:"):
                errors += 1
            elif ok:
                changed += 1
            else:
                skipped += 1

    return {"total": total, "changed": changed, "skipped": skipped, "errors": errors}


def run_config_wizard(cfg_dir: Path, existing: Optional[AppConfig]) -> AppConfig:
    clear()
    print(BANNER)
    print(" Music Genre Enforcer - Configuration")
    print(BANNER)
    print(f"Config dir: {cfg_dir}")
    if existing:
        print(f"Current library base: {existing.library_base}")
    print()

    base = Path(prompt("Base library path", str(existing.library_base) if existing else "")).expanduser()
    exts = prompt("Supported extensions (comma-separated)", ",".join(sorted(existing.supported_exts)) if existing else ",".join(sorted(SUPPORTED_EXTS_DEFAULT)))
    supported_exts = normalize_exts([x for x in exts.split(",") if x.strip()])
    dry = prompt_yes_no("Dry run (do not write tags)?", default=existing.dry_run if existing else False)

    cfg = AppConfig(library_base=base, supported_exts=supported_exts, dry_run=dry)
    save_config(cfg_dir, cfg)
    print("\nSaved config.")
    pause()
    return cfg


def prompt_for_new_artists(defs: Dict[str, str], artist_names: List[str]) -> Dict[str, str]:
    new = [a for a in artist_names if not defs.get(a, "").strip()]
    if not new:
        print("No new artists to define.")
        return defs

    def unique_genres() -> List[str]:
        genres = sorted({g.strip() for g in defs.values() if g and g.strip()}, key=str.casefold)
        return genres

    def print_genre_list(genres: List[str]) -> None:
        if not genres:
            print("  (No genres defined yet.)")
            return
        print("Existing genres:")
        for i, g in enumerate(genres, 1):
            print(f"  [{i}] {g}")

    print(f"New artists needing genre: {len(new)}")
    print("Enter a single genre, or commands:")
    print("  l = list existing genres")
    print("  p = pick from existing genres")
    print("  ? = help")
    print("  s = skip this artist")
    print()

    for artist in sorted(new, key=str.casefold):
        while True:
            raw = input(f"Genre for [{artist}] (or l/p/?/s): ").strip()

            if raw.lower() in ("s", "skip"):
                break

            if raw == "?" or raw.lower() in ("h", "help"):
                print("Commands:")
                print("  l  - list existing unique genres")
                print("  p  - pick a genre by number from the list")
                print("  s  - skip this artist for now")
                print("Or type a new genre as free text.")
                continue

            if raw.lower() in ("l", "list"):
                print_genre_list(unique_genres())
                continue

            if raw.lower() in ("p", "pick"):
                genres = unique_genres()
                if not genres:
                    print("No existing genres to pick from yet.")
                    continue
                print_genre_list(genres)
                sel = input("Pick number (blank cancels): ").strip()
                if sel == "":
                    continue
                if not sel.isdigit() or not (1 <= int(sel) <= len(genres)):
                    print("Invalid selection.")
                    continue
                chosen = genres[int(sel) - 1]
                defs[artist] = chosen
                print(f"  -> Set [{artist}] = {chosen}")
                break

            # free text genre
            if raw == "":
                # treat blank as skip, same as before
                break

            err = validate_single_genre(raw)
            if err:
                print(f"  ! {err}")
                continue

            defs[artist] = raw.strip()
            break

    return defs


def list_defined_artists(defs: Dict[str, str]) -> List[Tuple[str, str]]:
    """Return sorted list of (artist, genre) where genre is non-empty."""
    items = [(a, g.strip()) for a, g in defs.items() if g and g.strip()]
    items.sort(key=lambda x: x[0].casefold())
    return items


def edit_defined_artist_picker(defs: Dict[str, str], cfg_dir: Path) -> Dict[str, str]:
    """
    Numbered list picker for editing existing artist->genre mappings.
    Commands:
      - number: edit that entry
      - d <number>: delete definition
      - q: quit back to menu
    """
    items = list_defined_artists(defs)
    if not items:
        print("No existing definitions to edit.")
        pause()
        return defs

    while True:
        clear()
        print(BANNER)
        print(" Edit existing artist genre")
        print(BANNER)
        print("Select an artist number to edit.")
        print("Commands: [number]=edit | d [number]=delete | q=back")
        print(BANNER)

        # Print list
        for idx, (artist, genre) in enumerate(items):
            print(f"[{idx:>4}] {artist}  ->  {genre}")

        print()
        cmd = input("Choice: ").strip()

        if cmd.lower() in ("q", "quit", "back"):
            return defs

        # delete command: d 12
        if cmd.lower().startswith("d "):
            n = cmd[2:].strip()
            if not n.isdigit():
                print("Invalid delete command. Use: d <number>")
                pause()
                continue
            i = int(n)
            if not (0 <= i < len(items)):
                print("Out of range.")
                pause()
                continue
            artist, genre = items[i]
            if prompt_yes_no(f"Delete definition for '{artist}' ({genre})?", default=False):
                defs[artist] = ""
                save_definitions(cfg_dir, defs)  # persist immediately
                # refresh list
                items = list_defined_artists(defs)
            continue

        # edit by number
        if not cmd.isdigit():
            print("Invalid input.")
            pause()
            continue

        i = int(cmd)
        if not (0 <= i < len(items)):
            print("Out of range.")
            pause()
            continue

        artist, genre = items[i]
        clear()
        print(BANNER)
        print(f" Editing: {artist}")
        print(BANNER)
        print(f"Current genre: {genre}")
        print()

        while True:
            new_g = prompt("New genre (blank cancels)", genre).strip()
            if new_g == "":
                break
            err = validate_single_genre(new_g)
            if err:
                print(f"  ! {err}")
                continue
            defs[artist] = new_g
            save_definitions(cfg_dir, defs)  # persist immediately
            print("Saved.")
            pause()
            break

        # refresh list (in case of changes)
        items = list_defined_artists(defs)


def menu(cfg_dir: Path) -> int:
    cfg = load_config(cfg_dir)
    if cfg is None:
        cfg = run_config_wizard(cfg_dir, None)

    defs = load_definitions(cfg_dir)

    while True:
        clear()
        print(BANNER)
        print(" Music Genre Enforcer")
        print(BANNER)
        print(f"Library base : {cfg.library_base}")
        print(f"Dry run      : {cfg.dry_run}")
        print(f"Definitions  : {len([k for k, v in defs.items() if v.strip()])} artists defined")
        print(f"Config files : {cfg_dir}")
        print(BANNER)

        print("1) Configure (edit base path/extensions/dry-run)")
        print("2) Scan artists and prompt for NEW artist genres")
        print("3) Smart apply (only artists modified since last run)")
        print("4) Full apply (check ALL artists - slower but thorough)")
        print("5) List undefined artists")
        print("6) Edit an existing artist genre")
        print("7) Toggle dry-run")
        print("q) Quit")
        print()

        choice = input("Choice: ").strip().lower()

        if choice == "q":
            return 0

        if choice == "1":
            cfg = run_config_wizard(cfg_dir, cfg)
            continue

        if choice in ("2", "3", "4", "5"):
            base = cfg.library_base
            if not base.exists():
                print(f"Library base does not exist: {base}")
                pause()
                continue

            artist_dirs = discover_artist_dirs(base)
            artist_names = [p.name for p in artist_dirs]
            name_to_dir = {p.name: p for p in artist_dirs}

            if choice == "2":
                defs = prompt_for_new_artists(defs, artist_names)
                save_definitions(cfg_dir, defs)
                print("\nSaved definitions.")
                pause()
                continue

            if choice == "5":
                undefined = sorted([a for a in artist_names if not defs.get(a, "").strip()], key=str.casefold)
                if not undefined:
                    print("No undefined artists.")
                else:
                    print(f"Undefined artists ({len(undefined)}):")
                    for a in undefined:
                        print(f"  - {a}")
                pause()
                continue

            if choice in ("3", "4"):
                smart_mode = (choice == "3")
                defined_items = [(a, g) for a, g in defs.items() if g.strip() and a in name_to_dir]
                defined_items.sort(key=lambda x: x[0].casefold())
                if not defined_items:
                    print("No defined artists found on disk to apply.")
                    pause()
                    continue

                # For smart mode, filter to only modified artists
                last_apply = load_last_apply_time(cfg_dir)
                if smart_mode and last_apply is not None:
                    print(f"Checking for artists modified since last apply...")
                    modified_items = []
                    for artist, genre in defined_items:
                        artist_dir = name_to_dir[artist]
                        if has_changes_since(artist_dir, last_apply):
                            modified_items.append((artist, genre))
                    
                    if not modified_items:
                        print(f"\nNo artists have been modified since last apply.")
                        print("Use option 4 (Full apply) to force check all artists.")
                        pause()
                        continue
                    
                    print(f"Found {len(modified_items)} modified artist(s) out of {len(defined_items)} total.")
                    items_to_process = modified_items
                else:
                    if smart_mode and last_apply is None:
                        print("No previous apply found - will process all artists.")
                    items_to_process = defined_items

                mode_str = "smart" if smart_mode else "full"
                if not prompt_yes_no(f"Apply genre tags to {len(items_to_process)} artists ({mode_str} mode)?", default=True):
                    continue

                run_start_time = time.time()

                print("\nApplying...\n")
                totals = {"total": 0, "changed": 0, "skipped": 0, "errors": 0}
                total_artists = len(items_to_process)
                for i, (artist, genre) in enumerate(items_to_process, 1):
                    print(f"[{i}/{total_artists}] Processing {artist}...", end="", flush=True)
                    artist_dir = name_to_dir[artist]
                    counts = apply_genre_to_artist_dir(artist_dir, genre, cfg)
                    totals["total"] += counts["total"]
                    totals["changed"] += counts["changed"]
                    totals["skipped"] += counts["skipped"]
                    totals["errors"] += counts["errors"]
                    print(f" done (files={counts['total']} changed={counts['changed']} errors={counts['errors']})")

                print("\nTotals:")
                print(f"  total={totals['total']} changed={totals['changed']} skipped={totals['skipped']} errors={totals['errors']}")
                if cfg.dry_run:
                    print("\nNOTE: dry-run is ON, so nothing was written.")
                else:
                    # Save the apply timestamp for smart mode
                    save_last_apply_time(cfg_dir, run_start_time)
                    print(f"\nTimestamp saved for smart apply.")
                pause()
                continue

        if choice == "6":
            defs = edit_defined_artist_picker(defs, cfg_dir)
            # definitions already saved inside the picker
            continue

        if choice == "7":
            cfg.dry_run = not cfg.dry_run
            save_config(cfg_dir, cfg)
            print(f"Dry-run is now: {cfg.dry_run}")
            pause()
            continue

        print("Invalid choice.")
        pause()


def main() -> int:
    cfg_dir = config_dir()
    return menu(cfg_dir)


if __name__ == "__main__":
    raise SystemExit(main())
