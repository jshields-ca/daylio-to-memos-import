# daylio-to-memos-import

A command-line tool to import your [Daylio](https://daylio.net/) journal entries into a self-hosted [Memos](https://usememos.com/) instance.

## What gets imported

| Daylio data | Memos result |
|-------------|--------------|
| Entry note text | Memo content body |
| Entry title (`note_title`) | First line of memo content |
| Entry date & time | Memo `displayTime` (via PATCH after creation) |
| Activities / tags | `#tagname` hashtags appended to content |
| Mood | `#mood-rad` / `#mood-good` / `#mood-meh` / `#mood-bad` / `#mood-awful` hashtag |
| All imported entries | `#daylio-import` marker tag (for idempotency) |

### Not imported
- **Photos / assets** — Daylio entries with attached photos will still be imported as text; a warning is printed for each entry with photos.
- **Goals** — Daylio's goal-tracking data has no equivalent in Memos.

## Prerequisites

- Python 3.8 or later
- A running Memos instance with API access
- Your Memos API token (Settings → API Tokens)

## Installation

```bash
git clone https://github.com/jshields-ca/daylio-to-memos-import.git
cd daylio-to-memos-import
pip install -r requirements.txt
```

## Exporting from Daylio

1. Open Daylio on your phone
2. Go to **More** → **Backup & Restore** → **Export** (or **Advanced Backup**)
3. Save the `.daylio` file and transfer it to your computer

## Usage

```bash
python import.py --daylio backup.daylio \
                 --memos-url https://memos.example.com \
                 --token YOUR_MEMOS_API_TOKEN
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--daylio FILE` | *(required)* | Path to the `.daylio` backup file |
| `--memos-url URL` | *(required)* | Base URL of your Memos instance |
| `--token TOKEN` | `$MEMOS_TOKEN` | Memos API bearer token |
| `--visibility` | `PRIVATE` | Visibility of imported memos: `PRIVATE`, `PROTECTED`, or `PUBLIC` |
| `--dry-run` | off | Preview what would be imported without making any API calls |
| `--skip-mood` | off | Omit the `#mood-X` hashtag from memo content |
| `--skip-tags` | off | Omit Daylio activity tags as hashtags from memo content |
| `--delay SECONDS` | `0.5` | Pause between API calls (set to `0` to disable) |

### Recommended workflow

**1. Preview first with `--dry-run`**

```bash
python import.py --daylio backup.daylio \
                 --memos-url https://memos.example.com \
                 --token YOUR_TOKEN \
                 --dry-run
```

This prints every entry's content and timestamp without touching your Memos instance. Review the output to confirm tags, moods, and formatting look correct.

**2. Run the real import**

```bash
python import.py --daylio backup.daylio \
                 --memos-url https://memos.example.com \
                 --token YOUR_TOKEN
```

All entries are imported oldest-first as `PRIVATE` memos by default.

**3. Verify in Memos**

Search for `#daylio-import` in Memos to see all imported entries.

### Keeping your token out of shell history

Pass the token via an environment variable instead of the `--token` flag:

```bash
export MEMOS_TOKEN=your_token_here
python import.py --daylio backup.daylio --memos-url https://memos.example.com
```

## How timestamps work

Memos requires a two-step process for historical timestamps:

1. `POST /api/v1/memos` — creates the memo (always uses the current time initially)
2. `PATCH /api/v1/{memo_name}?updateMask=displayTime` — updates `displayTime` to the original Daylio entry date

If the PATCH step fails (some older Memos versions have known issues with `displayTime`), the memo is still imported successfully — you'll see a `WARN` message. The content and tags will be correct; only the display date may show as today's date. Upgrading to a recent Memos release resolves this.

## Re-running / idempotency

Every imported memo contains the `#daylio-import` hashtag. If you need to re-import, search Memos for `#daylio-import` to identify and delete previously imported entries before running again.

## Known limitations

- **No bulk import API in Memos** — entries are created one-by-one; large imports may take a few minutes
- **Photo/asset import not supported** — only the text content of entries is imported
- **Timestamp PATCH may not work on older Memos versions** — upgrade Memos if entry dates appear as today

## License

MIT — see [LICENSE](LICENSE)
