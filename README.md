![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)
![GitHub issues](https://img.shields.io/github/issues/jshields-ca/daylio-to-memos-import)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)

# daylio-to-memos-import

A command-line tool to import your [Daylio](https://daylio.net/) journal entries into a self-hosted [Memos](https://usememos.com/) instance.

---

## ⚠️ Required Memos setting

Before importing, enable **"Display edited time"** in Memos so that historical entry dates are shown in the timeline and calendar:

**Settings → System → Display with updated time** (toggle on)

Without this, all imported memos will appear with today's date in the timeline, even though the correct original date is stored internally.

---

## What gets imported

| Daylio data | Memos result |
|-------------|--------------|
| Entry note text | Memo content body |
| Entry title (`note_title`) | First line of memo content |
| Entry date & time | Memo `displayTime` (set at creation + confirmed via PATCH) |
| Activities / tags | `#tagname` hashtags appended to content |
| Mood | `#mood-rad` / `#mood-good` / `#mood-meh` / `#mood-bad` / `#mood-awful` hashtag |
| HTML formatting in notes | Converted to Markdown (`<b>` → **bold**, `<br>` → newline, etc.) |
| All imported entries | `#daylio-import` marker tag |

### Not imported

- **Photos / assets** — entries with attached photos are imported as text only; a warning is printed for each affected entry.
- **Goals** — Daylio's goal-tracking data has no equivalent in Memos.

---

## Prerequisites

- Python 3.8 or later
- A running Memos instance with API access
- Your Memos API token (**Settings → API Tokens**)

---

## Installation

```bash
git clone https://github.com/jshields-ca/daylio-to-memos-import.git
cd daylio-to-memos-import
pip install -r requirements.txt
```

---

## Exporting from Daylio

1. Open Daylio on your phone
2. Go to **More → Backup & Restore → Export** (or **Advanced Backup**)
3. Save the `.daylio` file and transfer it to your computer

---

## Usage

```bash
python import.py --daylio backup.daylio \
                 --memos-url https://memos.example.com \
                 --token YOUR_MEMOS_API_TOKEN
```

### All options

#### Import options

| Flag | Default | Description |
|------|---------|-------------|
| `--daylio FILE` | *(required)* | Path to the `.daylio` backup file |
| `--memos-url URL` | *(required)* | Base URL of your Memos instance |
| `--token TOKEN` | `$MEMOS_TOKEN` | Memos API bearer token |
| `--visibility` | `PRIVATE` | Visibility of imported memos: `PRIVATE`, `PROTECTED`, or `PUBLIC` |
| `--dry-run` | off | Preview what would be imported without making any API calls |
| `--skip-mood` | off | Omit the `#mood-X` hashtag from memo content |
| `--skip-tags` | off | Omit Daylio activity tags as hashtags from memo content |
| `--skip-empty` | off | Skip entries that have no note or title (mood/check-in only entries) |
| `--delay SECONDS` | `0.5` | Pause between API calls — use `0` to disable |

#### Re-run / deduplication options

| Flag | Default | Description |
|------|---------|-------------|
| `--state-file PATH` | `daylio-import-state.json` | Path to the local state file that tracks imported entries across runs |
| `--ignore-state` | off | Import all entries regardless of the state file (state file is still updated) |
| `--no-state` | off | Disable state tracking entirely — no dedup, no state file read or written |

#### Cleanup options

| Flag | Default | Description |
|------|---------|-------------|
| `--delete-imported` | off | Delete all memos tagged `#daylio-import` from Memos. Interactive — requires typing `YES` to confirm. `--daylio` is not required when this flag is used. Combine with `--dry-run` to preview what would be deleted. |

---

### Recommended workflow

**1. Preview with `--dry-run`**

```bash
python import.py --daylio backup.daylio \
                 --memos-url https://memos.example.com \
                 --token YOUR_TOKEN \
                 --dry-run
```

Prints every entry's content and timestamp without touching your Memos instance. Review the output to confirm tags, moods, and formatting look correct before committing.

**2. Enable "Display edited time" in Memos**

Go to **Settings → System** and enable **Display with updated time**. This ensures the timeline and calendar show the original Daylio entry dates instead of the import date.

**3. Run the real import**

```bash
python import.py --daylio backup.daylio \
                 --memos-url https://memos.example.com \
                 --token YOUR_TOKEN
```

All entries are imported oldest-first as `PRIVATE` memos by default. A local state file (`daylio-import-state.json`) is created to prevent duplicates if you re-run.

**4. Verify in Memos**

Search for `#daylio-import` in Memos to see all imported entries. Check a few entries to confirm the dates, tags, and content look correct.

---

### Deleting imported memos

If you need to undo an import or start fresh:

```bash
# Preview what would be deleted (no changes made)
python import.py --memos-url https://memos.example.com \
                 --token YOUR_TOKEN \
                 --delete-imported --dry-run

# Actually delete (interactive — prompts for YES confirmation)
python import.py --memos-url https://memos.example.com \
                 --token YOUR_TOKEN \
                 --delete-imported
```

You will also be offered the option to clear the local state file so you can re-import cleanly.

---

### Re-running safely

The state file (`daylio-import-state.json` by default) tracks which entries have been imported by their Daylio timestamp. On subsequent runs, already-imported entries are automatically skipped. The summary line shows how many were skipped:

```
Import complete: 502 succeeded, 0 failed, 360 skipped (empty), 0 skipped (already imported).
```

To force a full re-import (e.g. after deleting all memos and clearing the state file):

```bash
python import.py --daylio backup.daylio \
                 --memos-url https://memos.example.com \
                 --token YOUR_TOKEN \
                 --no-state
```

---

### Keeping your token out of shell history

```bash
export MEMOS_TOKEN=your_token_here
python import.py --daylio backup.daylio --memos-url https://memos.example.com
```

---

## How timestamps work

Memos uses two steps to set historical entry dates:

1. **POST** `/api/v1/memos` — creates the memo with `displayTime` set to the original Daylio entry date
2. **PATCH** `/api/v1/{memo_name}` — confirms `displayTime` via the API update mask

If the PATCH step fails for any reason, you will see a `WARN` message in the output. The memo is still imported; only the display date may show as today's date in that case.

> **Note:** Memos must be configured to show `displayTime` in the timeline. Enable **"Display with updated time"** in **Settings → System** to ensure the original Daylio dates appear rather than the server import time.

### Content length limit

If entries fail with an HTTP 400 "content too long" error, increase Memos's content length limit: **Settings → System → Content length limit (byte)** — set to `15000` or higher.

---

## Known limitations

- **No bulk import API in Memos** — entries are created one-by-one; large imports may take several minutes
- **Photo/asset import not supported** — only the text content of entries is imported
- **Timestamp display requires a Memos setting** — enable "Display with updated time" in Memos → Settings → System

---

## License

MIT — see [LICENSE](LICENSE)

---

## Author

Created by **[jshields-ca](https://github.com/jshields-ca)** · [scootr.ca](https://scootr.ca)

Found a bug or have a feature request? Please [open a GitHub issue](https://github.com/jshields-ca/daylio-to-memos-import/issues). Pull requests are welcome — see the issues list for ideas, or open one to discuss your change before submitting.

---

## AI disclosure

This script was developed with the assistance of AI tools (Claude by Anthropic) for code generation, debugging, and testing. All code has been reviewed and tested against a live Memos instance.
