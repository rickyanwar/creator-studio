"""Recover a billed Flow clip from the project listing when the submit reply was lost."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import sys

log = logging.getLogger(__name__)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def extract_entry_fields(entry: list) -> dict | None:
    """Extract media_id, workflow_id, created timestamp, and title from a Zzl0ze entry."""
    if not isinstance(entry, list):
        return None
    uuids: list[str] = []
    created: int | None = None
    title: str | None = None

    def walk(obj, parent_list=None, parent_idx=None):
        nonlocal created, title
        if isinstance(obj, str):
            if UUID_RE.match(obj):
                uuids.append(obj)
        elif isinstance(obj, list):
            if (
                created is None
                and len(obj) == 2
                and isinstance(obj[0], int)
                and isinstance(obj[1], int)
                and not isinstance(obj[0], bool)
                and not isinstance(obj[1], bool)
            ):
                created = obj[0]
                if parent_list is not None and parent_idx is not None and parent_idx > 0:
                    prev = parent_list[parent_idx - 1]
                    if isinstance(prev, str):
                        title = prev
                return
            for idx, item in enumerate(obj):
                walk(item, obj, idx)

    walk(entry)
    if not uuids or created is None:
        return None
    return {
        "media_id": uuids[0],
        "workflow_id": uuids[1] if len(uuids) > 1 else None,
        "created": created,
        "title": title,
    }


def _find_entries_in_obj(data) -> list | None:
    """Recursively locate the clip entry list in parsed JSON data structures."""
    if isinstance(data, list):
        if data and isinstance(data[0], list):
            if data[0] and isinstance(data[0][0], str) and UUID_RE.match(data[0][0]):
                return data
        # The real Zzl0ze payload starts with null: [null, [[<media_id>, ...]], ...] — search every item.
        if data:
            for item in data:
                if (
                    isinstance(item, list)
                    and len(item) >= 3
                    and item[0] == "wrb.fr"
                    and item[1] == "Zzl0ze"
                ):
                    payload = item[2]
                    if isinstance(payload, str):
                        try:
                            inner = json.loads(payload)
                            found = _find_entries_in_obj(inner)
                            if found:
                                return found
                        except Exception:
                            pass
                    elif isinstance(payload, (list, dict)):
                        found = _find_entries_in_obj(payload)
                        if found:
                            return found
                found = _find_entries_in_obj(item)
                if found:
                    return found
    elif isinstance(data, dict):
        for val in data.values():
            found = _find_entries_in_obj(val)
            if found:
                return found
    return None


def _extract_raw_entries(raw_text: str) -> list:
    """Extract raw entry lists from JSON or batchexecute response text."""
    text = raw_text.strip()
    if text.startswith(")]}'"):
        text = text[4:].strip()

    try:
        data = json.loads(text)
        found = _find_entries_in_obj(data)
        if found:
            return found
    except Exception:
        pass

    match = re.search(r'\["wrb\.fr",\s*"Zzl0ze",\s*"((?:[^"\\]|\\.)*)"', text)
    if match:
        try:
            payload_str = json.loads(f'"{match.group(1)}"')
            payload_data = json.loads(payload_str)
            found = _find_entries_in_obj(payload_data)
            if found:
                return found
        except Exception:
            pass

    idx = 0
    while True:
        pos = text.find('"Zzl0ze"', idx)
        if pos < 0:
            break
        quote_pos = text.find('"', pos + 8)
        if quote_pos >= 0:
            try:
                decoded_str, _ = json.JSONDecoder().raw_decode(text[quote_pos:])
                payload_data = json.loads(decoded_str)
                found = _find_entries_in_obj(payload_data)
                if found:
                    return found
            except Exception:
                pass
        idx = pos + 8

    return []


def parse_zzl0ze_entries(raw_text: str, frame_parser=None) -> list[dict]:
    """Parse raw batchexecute response text or payload text into structured clip entries."""
    if not raw_text or not raw_text.strip():
        return []
    raw_entries = []
    if callable(frame_parser):
        try:
            parsed = frame_parser(raw_text)
            raw_entries = _find_entries_in_obj(parsed) or []
        except Exception:
            raw_entries = []
    if not raw_entries:
        raw_entries = _extract_raw_entries(raw_text)

    entries = []
    for item in raw_entries:
        entry = extract_entry_fields(item)
        if entry:
            entries.append(entry)
    return entries


def filter_candidates(
    entries: list[dict], submit_epoch: float, exclude: list[str] | set[str] | None = None
) -> list[dict]:
    """Filter entries created within window and not excluded; sort newest first."""
    exclude_set = set(exclude or [])
    min_epoch = submit_epoch - 90
    candidates = [
        e
        for e in entries
        if e.get("media_id")
        and e.get("created") is not None
        and e["created"] >= min_epoch
        and e["media_id"] not in exclude_set
    ]
    candidates.sort(key=lambda e: e["created"], reverse=True)
    return candidates


def is_zzl0ze_response(response) -> bool:
    """Check if a response or its request targets the Zzl0ze listing RPC."""
    if getattr(response, "is_zzl0ze", False):
        return True
    if getattr(response, "name", "") == "Zzl0ze":
        return True
    url = getattr(response, "url", "") or ""
    if callable(url):
        try:
            url = url()
        except Exception:
            url = ""
    if "Zzl0ze" in url:
        return True
    req = getattr(response, "request", None)
    if req is not None:
        req_url = getattr(req, "url", "") or ""
        if callable(req_url):
            try:
                req_url = req_url()
            except Exception:
                req_url = ""
        if "Zzl0ze" in req_url:
            return True
    return False


async def _navigate_load(page, project_id: str, attempt: int):
    """Perform initial goto or subsequent reload on the project page."""
    try:
        if attempt == 1 or not hasattr(page, "reload"):
            url = f"https://flow.google.com/project/{project_id}"
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        else:
            await page.reload(wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:
        sys.stderr.write(f"page navigation error (attempt {attempt}): {exc}\n")


async def _wait_for_listing(attempt_state: dict, wait_s: float, sleep) -> None:
    """Wait for listing entries to arrive or until wait_s elapses."""
    elapsed = 0.0
    step = 0.5
    while True:
        if len(attempt_state["entries"]) >= 1 or elapsed >= wait_s:
            break
        await sleep(step)
        elapsed += step


def _attempt_status(attempt_state: dict) -> str:
    """Determine listing status for an attempt."""
    if attempt_state["read"]:
        return "read"
    if attempt_state["unreadable"] > 0:
        return "unreadable"
    return "missing"


async def collect_listing(
    page,
    project_id: str,
    attempts: int = 4,
    wait_s: float = 20.0,
    sleep=asyncio.sleep,
    frame_parser=None,
) -> tuple[list[dict], int, str | None]:
    """Collect Zzl0ze listing entries with retry across up to `attempts` page loads."""
    attempt_state: dict = {"entries": [], "read": False, "unreadable": 0}

    async def on_response(response):
        url = getattr(response, "url", "") or ""
        if callable(url):
            try:
                url = url()
            except Exception:
                url = ""
        is_zz = is_zzl0ze_response(response)
        if "batchexecute" not in url and not is_zz:
            return
        try:
            text = await response.text()
            if "Zzl0ze" in text:
                attempt_state["read"] = True
                parsed = parse_zzl0ze_entries(text, frame_parser=frame_parser)
                existing = {e["media_id"] for e in attempt_state["entries"] if "media_id" in e}
                for item in parsed:
                    mid = item.get("media_id")
                    if mid and mid not in existing:
                        existing.add(mid)
                        attempt_state["entries"].append(item)
        except Exception:
            if is_zz:
                attempt_state["unreadable"] += 1

    page.on("response", on_response)
    try:
        for attempt in range(1, attempts + 1):
            attempt_state["entries"] = []
            attempt_state["read"] = False
            attempt_state["unreadable"] = 0
            await _navigate_load(page, project_id, attempt)
            await _wait_for_listing(attempt_state, wait_s, sleep)

            status = _attempt_status(attempt_state)
            sys.stderr.write(
                f"attempt {attempt}: zzl0ze={status} entries={len(attempt_state['entries'])}\n"
            )
            if len(attempt_state["entries"]) >= 1:
                return attempt_state["entries"], attempt, None
            if attempt < attempts:
                await sleep(3.0)

        return [], attempts, f"listing unreadable after {attempts} loads"
    finally:
        if hasattr(page, "remove_listener"):
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass


async def try_recover_candidates(
    recover_clip_fn, page, project_id: str, candidates: list[dict], out_dir: Path,
    tries_per_candidate: int = 3, new_page=None,
) -> bool:
    """Recover the newest candidate clip; each candidate gets several tries because Chrome can lose the
    clip route's as29s reply body too (same bug as the lost submit reply). Fresh tab per try when possible."""
    for idx, candidate in enumerate(candidates[:3]):
        mid = candidate["media_id"]
        for attempt in range(1, tries_per_candidate + 1):
            work_page = page
            if new_page is not None:
                try:
                    work_page = await new_page()
                except Exception:
                    work_page = page
            try:
                clip = await recover_clip_fn(work_page, project_id=project_id, media_id=mid, out_dir=out_dir)
                if clip and getattr(clip, "path", None):
                    print(f"RECOVERED {clip.media_id} {clip.workflow_id} {clip.path} {clip.bytes}")
                    return True
            except Exception as exc:
                sys.stderr.write(f"recover_clip failed for {mid} (candidate {idx + 1}, try {attempt}): {exc}\n")
            finally:
                if work_page is not page:
                    try:
                        await work_page.close()
                    except Exception:
                        pass
    return False


async def run_recovery(
    profile: str, project_id: str, submit_epoch: float, out_dir: Path, exclude: list[str]
):
    """Run Flow browser session, capture Zzl0ze RPC, and recover candidate clip."""
    from gflow_cli._cli_helpers import _make_provider_dir
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.transports.migrated_recover import recover_clip

    try:
        from gflow_cli.api.transports.migrated_recover import parse_frames
    except (ImportError, AttributeError):
        parse_frames = None

    out_dir.mkdir(parents=True, exist_ok=True)
    async with FlowApiClient(profile_dir=_make_provider_dir(profile), headless=False) as client:
        entries, attempts_used, reason = await collect_listing(
            client.page, project_id, attempts=4, wait_s=20.0, frame_parser=parse_frames
        )
        if not entries:
            print(f"NONE {reason or 'listing unreadable after 4 loads'}")
            return

        seen_ids = set()
        deduped = []
        for e in entries:
            if e["media_id"] not in seen_ids:
                seen_ids.add(e["media_id"])
                deduped.append(e)

        candidates = filter_candidates(deduped, submit_epoch, exclude)
        if not candidates:
            sys.stderr.write(f"No candidates found (total entries: {len(deduped)})\n")
            print("NONE no candidates found")
            return

        recovered = await try_recover_candidates(
            recover_clip, client.page, project_id, candidates, out_dir,
            new_page=client._context.new_page,
        )
        if not recovered:
            print("NONE recovery failed for all candidates")


def main():
    """CLI entrypoint for project_recover.py."""
    if len(sys.argv) < 5:
        sys.stderr.write(
            "Usage: python project_recover.py <profile> <project_id> <submit_epoch> <out_dir> [<exclude_media_id> ...]\n"
        )
        print("NONE invalid arguments")
        sys.exit(0)
    profile = sys.argv[1]
    project_id = sys.argv[2]
    try:
        submit_epoch = float(sys.argv[3])
    except ValueError:
        print("NONE invalid submit_epoch")
        sys.exit(0)
    out_dir = Path(sys.argv[4])
    exclude = sys.argv[5:]

    try:
        asyncio.run(run_recovery(profile, project_id, submit_epoch, out_dir, exclude))
    except Exception as exc:
        sys.stderr.write(f"fatal error: {exc}\n")
        print(f"NONE fatal error: {exc}")
    sys.exit(0)


if __name__ == "__main__":
    main()
