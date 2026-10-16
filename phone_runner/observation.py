from datetime import datetime, timezone
import json
import logging
import os
import re

from phone_runner.adb import query_ui_hierarchy, run_adb

logger = logging.getLogger("phone_runner.observation")


def _prune_old_observations(obs_dir: str, keep: int = 20) -> None:
    try:
        files: list[tuple[float, str, str]] = []
        for name in os.listdir(obs_dir):
            full_path = os.path.join(obs_dir, name)
            if os.path.isfile(full_path):
                try:
                    mtime = os.path.getmtime(full_path)
                    files.append((mtime, name, full_path))
                except OSError:
                    pass
        if len(files) > keep:
            files.sort(key=lambda x: (x[0], x[1]))
            to_delete = files[: len(files) - keep]
            for _, _, p in to_delete:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except Exception as exc:
        logger.warning("Failed to prune old observations in %s: %s", obs_dir, exc)


def save_composer_observation(serial: str, reason: str) -> str | None:
    """Capture snapshot of modern composer state and save to local JSON file.

    Never raises: catches all errors, logs warning, returns file path or None.
    """
    try:
        obs_dir = os.environ.get("PHONE_RUNNER_OBSERVATION_DIR", "/var/lib/phone-runner/observations")
        os.makedirs(obs_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(obs_dir, 0o700)
        except OSError:
            pass

        now = datetime.now(timezone.utc)
        captured_at = now.isoformat()

        xml_res = query_ui_hierarchy(serial)
        if xml_res.returncode != 0 or not xml_res.stdout:
            logger.warning("save_composer_observation: UI hierarchy capture failed for %s: %s", serial, xml_res.stderr)
            return None

        portal_res = run_adb(
            ["shell", "content", "query", "--uri", "content://com.mobilerun.portal/state"],
            serial=serial,
            timeout=5.0,
        )
        focus_res = run_adb(
            ["shell", "dumpsys window | grep mCurrentFocus"],
            serial=serial,
            timeout=5.0,
        )
        ime_res = run_adb(
            ["shell", 'dumpsys input_method | grep -E "mCurSelectedId|mInputShown|mServedView"'],
            serial=serial,
            timeout=5.0,
        )

        payload = {
            "reason": reason,
            "captured_at": captured_at,
            "focus": (focus_res.stdout or "").strip(),
            "ime": (ime_res.stdout or "").strip(),
            "ui_xml": str(xml_res.stdout),
            "portal_state": (portal_res.stdout or "")[:200_000],
        }

        ts = now.strftime("%Y%m%dT%H%M%SZ")
        safe_reason = re.sub(r"[^a-zA-Z0-9_-]", "_", reason)
        filename = f"{ts}_{safe_reason}.json"
        filepath = os.path.join(obs_dir, filename)

        if os.path.exists(filepath):
            filename = f"{now.strftime('%Y%m%dT%H%M%S_%fZ')}_{safe_reason}.json"
            filepath = os.path.join(obs_dir, filename)

        fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        finally:
            pass

        try:
            os.chmod(filepath, 0o600)
        except OSError:
            pass

        _prune_old_observations(obs_dir, keep=20)
        return filepath
    except Exception as exc:
        logger.warning("save_composer_observation failed for %s: %s", serial, exc)
        return None
