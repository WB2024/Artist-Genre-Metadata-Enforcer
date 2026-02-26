# 🎵 Artist Genre Metadata Enforcer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-yellow?logo=buy-me-a-coffee)](https://buymeacoffee.com/succinctrecords)

**Your music library, your rules.**

A lightweight, filesystem-driven Python tool that enforces **your personal genre definitions** across your entire music library. No more "Alternative Rock" vs "Rock - Alternative" inconsistencies—define each artist's genre once, and let the tool handle the rest.

---

## ✨ Features

- 🎯 **Artist-based genre enforcement** — Define genre once per artist, apply to their entire discography
- 💾 **Persistent definitions** — Never re-prompt for artists you've already defined
- 🔄 **Incremental workflow** — Only prompts for new artists on subsequent runs
- ⚡ **Smart apply mode** — Only processes artists modified since last run (fast for large libraries)
- 🎨 **Reuse existing genres** — Pick from your already-defined genres to maintain consistency
- 🗂️ **Filesystem-driven** — No database; works directly with your folder structure
- 🏷️ **Universal format support** — Writes to MP3, FLAC, M4A, OGG, OPUS, and more via [Mutagen](https://mutagen.readthedocs.io/)
- 🔍 **Dry-run mode** — Preview changes before writing
- 🐧 **Debian-friendly** — Uses system Python packages (no pip/venv headaches)
- 📋 **Interactive CLI** — Clean menu-driven interface, no GUI dependencies
- 🚀 **Parallel processing** — Uses multiple threads for faster tagging

---

## 📦 Installation

### Prerequisites

- **Python 3.7+** (usually pre-installed on Debian/Ubuntu)
- **python3-mutagen** (for reading/writing audio tags)

### Step 1: Install dependencies

On **Debian/Ubuntu**:

```bash
sudo apt update
sudo apt install -y python3-mutagen
```

On **other distros** (using pip):

```bash
pip install mutagen
```

### Step 2: Download the script

```bash
sudo curl -o /usr/local/bin/music-genre-enforcer https://raw.githubusercontent.com/WB2024/Artist-Genre-Metadata-Enforcer/main/music-genre-enforcer.py
sudo chmod +x /usr/local/bin/music-genre-enforcer
```

Or clone the repo:

```bash
git clone https://github.com/WB2024/Artist-Genre-Metadata-Enforcer.git
cd Artist-Genre-Metadata-Enforcer
sudo cp music-genre-enforcer.py /usr/local/bin/music-genre-enforcer
sudo chmod +x /usr/local/bin/music-genre-enforcer
```

### Step 3: Run it

```bash
music-genre-enforcer
```

On first run, you'll be prompted to configure your library base path.

---

## � Updating

When a new version is released, simply re-download and replace the script:

### Quick update (one-liner)

```bash
sudo curl -o /usr/local/bin/music-genre-enforcer https://raw.githubusercontent.com/WB2024/Artist-Genre-Metadata-Enforcer/main/music-genre-enforcer.py && sudo chmod +x /usr/local/bin/music-genre-enforcer
```

### Or if you cloned the repo

```bash
cd Artist-Genre-Metadata-Enforcer
git pull
sudo cp music-genre-enforcer.py /usr/local/bin/music-genre-enforcer
sudo chmod +x /usr/local/bin/music-genre-enforcer
```

> **Note:** Your configuration and artist definitions in `~/.config/music-genre-enforcer/` are preserved—updating only replaces the script itself.

---

## �🚀 Usage

### Your library structure

The tool expects this folder hierarchy:

```
/your/music/library/
├── A/
│   ├── Aphex Twin/
│   │   ├── [1992] Selected Ambient Works 85-92/
│   │   │   ├── 01 - Xtal.flac
│   │   │   └── ...
│   └── ...
├── B/
│   ├── Bowie, David/
│   │   └── ...
├── #/
│   └── 2Pac/
│       └── ...
└── Various Artists/
    └── ...
```

Where:
- **Top level** = grouping folders (typically letters: `A`, `B`, `#`, `Various Artists`, etc.)
- **Second level** = artist folders
- **Third level+** = albums, discs, tracks

### Workflow

1. **Configure** your library base path (menu option `1`)
2. **Scan for new artists** and define genres (menu option `2`)
   - Type a genre as free text, **or**
   - Press `l` to list existing genres
   - Press `p` to pick from existing genres (keeps spelling/casing consistent)
   - Press `s` to skip an artist
3. **Apply genres** using smart or full mode:
   - **Smart apply** (option `3`) — Only processes artists with new/modified files since last run
   - **Full apply** (option `4`) — Checks all artists (use periodically for thorough verification)
   - Writes the `GENRE` tag to every audio file under each artist folder
   - Skips files that already have the correct genre

### Interactive prompts during artist definition

When prompted for a genre:

| Command | Action |
|---------|--------|
| *(type text)* | Define a new genre (free text) |
| `l` | List all your existing unique genres |
| `p` | Pick a genre by number from the list |
| `s` | Skip this artist for now |
| `?` | Show help |

**Example:**

```
Genre for [Aphex Twin] (or l/p/?/s): p
Existing genres:
  [1] Electronic - Ambient
  [2] Hip-Hop
  [3] Post-Punk
  [4] Rock - Art
Pick number (blank cancels): 1
  -> Set [Aphex Twin] = Electronic - Ambient
```

---

## 📋 Menu Options

```
1) Configure — Edit base library path, file extensions, dry-run mode
2) Scan artists and prompt for NEW artist genres
3) Smart apply — Only process artists modified since last run (fast)
4) Full apply — Check ALL artists (slower but thorough)
5) List undefined artists
6) Edit an existing artist genre (numbered picker)
7) Toggle dry-run (preview mode on/off)
q) Quit
```

---

## 🗂️ Configuration & Data

All settings and definitions are stored in:

```
~/.config/music-genre-enforcer/
├── config.json          # Library path, extensions, dry-run setting
├── artist_genres.json   # Your artist → genre mappings
└── last_apply.json      # Timestamp of last apply (for smart mode)
```

**Portable?** Yes—copy these files to another machine and your definitions travel with you.

---

## 🎯 Use Cases

### Problem: Inconsistent genres across your library
You ripped CDs, downloaded from different sources, used multiple taggers—now you have:
- "Hip-Hop" vs "Hip Hop" vs "Rap"
- "Rock - Art" vs "Art Rock" vs "Alternative"

**Solution:** Define your canonical genre **once per artist**, enforce it everywhere.

### Problem: MusicBrainz/streaming tags don't match your taste
As far as you're concerned, **all** 2Pac songs are `Hip-Hop` and **all** Velvet Underground songs are `Rock - Art`—regardless of what MusicBrainz says.

**Solution:** This tool enforces **your subjective opinion**, not a database's.

### Problem: Adding new music constantly
Every time you add a new artist, you'd have to manually tag every file.

**Solution:** Define the artist's genre once; the tool writes it to all files (even multi-disc sets with hundreds of tracks).

---

## 🛠️ Advanced

### Supported audio formats

Anything [Mutagen](https://mutagen.readthedocs.io/) supports in easy mode:

- MP3 (ID3v2)
- FLAC (Vorbis comments)
- M4A/MP4/ALAC (iTunes-style tags)
- OGG Vorbis, Opus
- WavPack, APE, ASF/WMA
- AIFF (experimental)

### Custom file extensions

Edit `~/.config/music-genre-enforcer/config.json`:

```json
{
  "library_base": "/your/music/path",
  "supported_exts": [".mp3", ".flac", ".m4a", ".ogg", ".opus"],
  "dry_run": false
}
```

Or use menu option `1` to configure interactively.

### Dry-run mode

Enable via menu option `6` or edit `config.json` to set `"dry_run": true`.

The tool will:
- Scan all files
- Report what *would* change
- **Not write anything** to disk

Perfect for testing before committing.

---

## 🐛 Troubleshooting

### "Missing dependency: mutagen"

**Debian/Ubuntu:**
```bash
sudo apt install python3-mutagen
```

**Other (pip):**
```bash
pip install mutagen
```

### "Library base does not exist"

Run menu option `1` (Configure) and double-check your path. Use **absolute paths** (e.g., `/srv/data/Music`).

### Files are "skipped" during apply

**This is normal.** Skipped = file already has the correct genre tag (no need to rewrite).

### Genre validation fails

The tool enforces **single genre only** (no `;`, `|`, or `/` separators).

If you need multi-genre tags, open an issue—I can add that as an option.

---

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📜 License

This project is licensed under the **MIT License**—see [LICENSE](LICENSE) for details.

You are free to use, modify, and distribute this software. Attribution appreciated but not required.

---

## ☕ Support

If this tool saved you hours of manual tagging, consider buying me a coffee:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-yellow?logo=buy-me-a-coffee)](https://buymeacoffee.com/succinctrecords)

---

## 🙏 Acknowledgments

- Built with [Mutagen](https://mutagen.readthedocs.io/)—the best Python audio tagging library
- Inspired by the frustration of managing 4,000+ track libraries with inconsistent metadata

---

## 📬 Contact

- **Author:** WB2024
- **GitHub:** [@WB2024](https://github.com/WB2024)
- **Issues:** [Report a bug or request a feature](https://github.com/WB2024/Artist-Genre-Metadata-Enforcer/issues)

---

**Happy tagging!** 🎶
