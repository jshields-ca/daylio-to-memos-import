#!/usr/bin/env python3
"""
import.py — Daylio to Memos import utility

Reads a Daylio .daylio backup file and creates corresponding memo entries
in a self-hosted Memos instance via its REST API.

Usage:
    python import.py --daylio backup.daylio --memos-url https://memos.example.com --token YOUR_TOKEN

    # Preview without making API calls:
    python import.py --daylio backup.daylio --memos-url https://... --token ... --dry-run

    # Omit mood/tag hashtags:
    python import.py ... --skip-mood --skip-tags

    # Set token via environment variable instead of CLI flag:
    export MEMOS_TOKEN=your_token_here
    python import.py --daylio backup.daylio --memos-url https://memos.example.com
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import traceback
import zipfile
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("Missing dependency: run 'pip install requests'", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MemosAPIError(Exception):
    """Raised when the Memos API returns a non-2xx response."""

    def __init__(self, message: str, status_code: int = 0, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


# ---------------------------------------------------------------------------
# Layer A — Backup Parsing
# ---------------------------------------------------------------------------

def open_daylio_backup(path: str) -> dict:
    """Open a .daylio backup file and return the parsed JSON root dict."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Backup file not found: {path}")

    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile:
        raise ValueError(f"Not a valid ZIP/daylio file: {path}")

    with zf:
        names = zf.namelist()
        # The main backup entry is typically named "backup.daylio"
        backup_member = None
        for name in names:
            if name.endswith("backup.daylio") or name == "backup.daylio":
                backup_member = name
                break

        if backup_member is None:
            raise ValueError(
                f"Could not find 'backup.daylio' inside the archive. "
                f"Members found: {names}"
            )

        raw_bytes = zf.read(backup_member)

    try:
        decoded = base64.b64decode(raw_bytes)
    except Exception as exc:
        raise ValueError(f"Failed to base64-decode backup content: {exc}") from exc

    try:
        data = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON from backup: {exc}") from exc

    for key in ("dayEntries", "customMoods", "tags"):
        if key not in data:
            raise ValueError(
                f"Backup JSON is missing expected key '{key}'. "
                "This may not be a valid Daylio backup."
            )

    return data


def build_mood_map(custom_moods: list) -> dict:
    """Return {mood_id: predefined_name} from the customMoods array."""
    mood_map = {}
    for mood in custom_moods:
        mood_id = mood.get("id")
        if mood_id is None:
            continue
        # predefined_name is e.g. "rad", "good", "meh", "bad", "awful"
        name = mood.get("predefined_name") or mood.get("custom_name") or "unknown"
        mood_map[mood_id] = name.lower().strip() or "unknown"
    return mood_map


def build_tag_map(tags: list) -> dict:
    """Return {tag_id: tag_name} from the tags array."""
    return {
        tag["id"]: tag.get("name", "")
        for tag in tags
        if "id" in tag
    }


# ---------------------------------------------------------------------------
# Layer B — Entry Transformation
# ---------------------------------------------------------------------------

def sanitize_tag(name: str) -> str:
    """
    Convert a Daylio tag name to a valid Memos hashtag word.
    - Lowercase
    - Spaces and underscores → hyphens
    - Strip characters that are not alphanumeric or hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    """
    name = name.lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9\-]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    return name or "tag"


def entry_to_timestamp(entry: dict) -> str:
    """
    Convert a Daylio entry's datetime (milliseconds) to an ISO 8601 UTC string.
    Returns e.g. "2023-06-15T09:30:00Z".
    """
    ms = entry.get("datetime", 0)
    dt = datetime.utcfromtimestamp(ms / 1000)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_content(entry: dict, mood_map: dict, tag_map: dict, args: argparse.Namespace) -> str:
    """
    Assemble the full Memos content string for a Daylio entry.

    Format:
        [optional title line]
        [blank line if title present]
        [note body]

        #mood-X #tag1 #tag2 #daylio-import
    """
    parts = []

    title = (entry.get("note_title") or "").strip()
    note = (entry.get("note") or "").strip()

    if title:
        parts.append(title)

    if note:
        parts.append(note)

    # Build hashtag line
    hashtags = []

    if not args.skip_mood:
        mood_id = entry.get("mood")
        mood_name = mood_map.get(mood_id, "unknown")
        sanitized = sanitize_tag(mood_name)
        hashtags.append(f"#mood-{sanitized}")

    if not args.skip_tags:
        # Deduplicate tag IDs while preserving order
        seen = {}
        for tag_id in entry.get("tags", []):
            if tag_id not in seen:
                seen[tag_id] = True
                tag_name = tag_map.get(tag_id, "")
                if tag_name:
                    sanitized = sanitize_tag(tag_name)
                    if sanitized:
                        hashtags.append(f"#{sanitized}")

    # Always include the import marker
    hashtags.append("#daylio-import")

    tag_line = " ".join(hashtags)

    if parts:
        # Separate body from tags with a blank line
        content = "\n\n".join(parts) + "\n\n" + tag_line
    else:
        content = tag_line

    return content


# ---------------------------------------------------------------------------
# Layer C — Memos API Client
# ---------------------------------------------------------------------------

class MemosClient:
    """Thin wrapper around requests.Session for the Memos v1 REST API."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        self._timeout = 30

    def _api(self, path: str) -> str:
        """Build an API URL. path should start with /."""
        return f"{self.base_url}{path}"

    def check_connectivity(self) -> None:
        """
        Test that the Memos instance is reachable and the token is valid.
        Raises MemosAPIError on auth failure or connection error.
        """
        url = self._api("/api/v1/memos")
        try:
            resp = self.session.get(url, params={"pageSize": 1}, timeout=self._timeout)
        except requests.exceptions.ConnectionError as exc:
            raise MemosAPIError(
                f"Could not connect to Memos at {self.base_url}. "
                "Check the URL and that the server is running."
            ) from exc
        except requests.exceptions.Timeout:
            raise MemosAPIError(f"Connection to {self.base_url} timed out.")

        if resp.status_code == 401:
            raise MemosAPIError(
                "Authentication failed (HTTP 401). Check your token.",
                status_code=401,
                response_text=resp.text,
            )
        if not resp.ok:
            raise MemosAPIError(
                f"Unexpected response from Memos (HTTP {resp.status_code}).",
                status_code=resp.status_code,
                response_text=resp.text,
            )

    def create_memo(self, content: str, visibility: str) -> dict:
        """
        POST /api/v1/memos to create a new memo.
        Returns the parsed JSON response dict.
        Raises MemosAPIError on non-2xx.
        """
        url = self._api("/api/v1/memos")
        payload = {"content": content, "visibility": visibility}
        try:
            resp = self.session.post(url, json=payload, timeout=self._timeout)
        except requests.exceptions.Timeout:
            raise MemosAPIError("Request timed out while creating memo.")
        except requests.exceptions.RequestException as exc:
            raise MemosAPIError(f"Network error while creating memo: {exc}") from exc

        if not resp.ok:
            raise MemosAPIError(
                f"Failed to create memo (HTTP {resp.status_code}): {resp.text[:200]}",
                status_code=resp.status_code,
                response_text=resp.text,
            )

        try:
            return resp.json()
        except ValueError:
            raise MemosAPIError(
                f"Memos returned non-JSON response: {resp.text[:200]}"
            )

    def patch_display_time(self, memo_name: str, display_time: str) -> bool:
        """
        PATCH /api/v1/{memo_name}?updateMask=displayTime to set the historical timestamp.

        Returns True on success, False on failure.
        Never raises — timestamp backfill is best-effort.

        memo_name: the resource name returned by create_memo, e.g. "memos/123"
        display_time: ISO 8601 UTC string, e.g. "2023-06-15T09:30:00Z"
        """
        url = self._api(f"/api/v1/{memo_name}")
        payload = {"displayTime": display_time}
        try:
            resp = self.session.patch(
                url,
                json=payload,
                params={"updateMask": "displayTime"},
                timeout=self._timeout,
            )
            if resp.ok:
                return True
            return False
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Layer D — Import Orchestration
# ---------------------------------------------------------------------------

def _entry_date_str(entry: dict) -> str:
    """Return a human-readable date string for an entry (for logging)."""
    try:
        ms = entry.get("datetime", 0)
        dt = datetime.utcfromtimestamp(ms / 1000)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "(unknown date)"


def run_import(args: argparse.Namespace) -> None:
    """Top-level import orchestration."""

    # --- Step 1: Parse backup ---
    print(f"Reading backup: {args.daylio}")
    try:
        data = open_daylio_backup(args.daylio)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    mood_map = build_mood_map(data.get("customMoods", []))
    tag_map = build_tag_map(data.get("tags", []))
    entries = data.get("dayEntries", [])

    print(f"Found {len(entries)} entries, {len(mood_map)} moods, {len(tag_map)} tags.")

    # --- Step 2: Connectivity check ---
    client = None
    if not args.dry_run:
        print(f"Connecting to Memos at {args.memos_url} ...")
        client = MemosClient(args.memos_url, args.token)
        try:
            client.check_connectivity()
        except MemosAPIError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print("Connection OK.")

    # --- Step 3: Sort entries oldest-first ---
    entries = sorted(entries, key=lambda e: e.get("datetime", 0))

    # --- Step 4: Import loop ---
    total = len(entries)
    succeeded = 0
    failed = 0
    failed_entries = []

    print()
    if args.dry_run:
        print("=== DRY RUN — no API calls will be made ===")
        print()

    for i, entry in enumerate(entries, start=1):
        entry_id = entry.get("id", "?")
        date_str = _entry_date_str(entry)
        prefix = f"[{i}/{total}]"

        # Warn about assets (photos) — not supported
        asset_count = len(entry.get("assets", []))
        if asset_count:
            print(f"{prefix} WARN: Entry {entry_id} ({date_str}) has {asset_count} "
                  f"asset(s) — photo import is not supported, skipping photos.")

        try:
            content = build_content(entry, mood_map, tag_map, args)
            display_time = entry_to_timestamp(entry)
        except Exception as exc:
            print(f"{prefix} ERROR: Could not build content for entry {entry_id} "
                  f"({date_str}): {exc}")
            failed += 1
            failed_entries.append((entry_id, date_str))
            continue

        if args.dry_run:
            print(f"{prefix} Entry {entry_id} | {date_str}")
            print(f"  displayTime: {display_time}")
            print(f"  content:\n    " + content.replace("\n", "\n    "))
            print()
            succeeded += 1
            continue

        # --- Live import ---
        print(f"{prefix} Importing entry {entry_id} ({date_str}) ...", end=" ", flush=True)

        try:
            memo = client.create_memo(content, args.visibility)
        except MemosAPIError as exc:
            print(f"ERROR: {exc}")
            failed += 1
            failed_entries.append((entry_id, date_str))
            continue
        except Exception as exc:
            print(f"ERROR (unexpected): {exc}")
            failed += 1
            failed_entries.append((entry_id, date_str))
            continue

        # Extract memo resource name (e.g. "memos/123")
        memo_name = memo.get("name", "")

        if memo_name:
            ok = client.patch_display_time(memo_name, display_time)
            if not ok:
                print(f"OK (WARN: could not set displayTime for {memo_name})")
            else:
                print("OK")
        else:
            print("OK (WARN: response missing 'name', could not set displayTime)")

        succeeded += 1

        if args.delay > 0:
            time.sleep(args.delay)

    # --- Step 5: Summary ---
    print()
    if args.dry_run:
        print(f"Dry run complete: {succeeded} entries would be imported.")
    else:
        print(f"Import complete: {succeeded} succeeded, {failed} failed.")
        if failed_entries:
            print("Failed entries:")
            for eid, dstr in failed_entries:
                print(f"  - Entry {eid} ({dstr})")


# ---------------------------------------------------------------------------
# Layer E — CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import Daylio journal entries into a self-hosted Memos instance.\n\n"
            "The MEMOS_TOKEN environment variable can be used instead of --token "
            "to avoid exposing the token in shell history."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--daylio",
        required=True,
        metavar="FILE",
        help="Path to the .daylio backup file exported from the Daylio app.",
    )
    parser.add_argument(
        "--memos-url",
        required=True,
        metavar="URL",
        help="Base URL of your Memos instance, e.g. https://memos.example.com",
    )

    env_token = os.environ.get("MEMOS_TOKEN", "")
    parser.add_argument(
        "--token",
        default=env_token or None,
        required=not bool(env_token),
        metavar="TOKEN",
        help=(
            "Bearer token for Memos API authentication. "
            "Can also be set via the MEMOS_TOKEN environment variable."
        ),
    )

    parser.add_argument(
        "--visibility",
        default="PRIVATE",
        choices=["PRIVATE", "PROTECTED", "PUBLIC"],
        help="Visibility level for imported memos. Default: PRIVATE",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be imported without making any API calls.",
    )
    parser.add_argument(
        "--skip-tags",
        action="store_true",
        help="Do not include Daylio activity tags as hashtags in the memo content.",
    )
    parser.add_argument(
        "--skip-mood",
        action="store_true",
        help="Do not include the Daylio mood as a #mood-X hashtag in the memo content.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="Seconds to sleep between API calls (default: 0.5). Use 0 to disable.",
    )

    args = parser.parse_args()
    # Normalise attribute name for use in code (memos_url vs memos-url)
    args.memos_url = args.memos_url.rstrip("/")
    return args


def main() -> None:
    try:
        args = parse_args()
        run_import(args)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        print("\nUnexpected error:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
