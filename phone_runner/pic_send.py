"""Ordered PIC message transport through the local Hermes CLI."""

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from phone_runner.recovery import get_hermes_path, hermes_command, sanitize_error

MAX_MEDIA_BYTES = 60 * 1024 * 1024
TARGET = re.compile(r"(whatsapp:\+?[0-9]{8,20}|telegram:(@[A-Za-z0-9_]{5,32}|-?[0-9]{5,20}))")


def validate_request(target, messages) -> str | None:
    if not isinstance(target, str) or not TARGET.fullmatch(target):
        return "invalid_target"
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        return "invalid_messages"
    for message in messages:
        if not isinstance(message, dict):
            return "invalid_messages"
        if message.get("kind") == "text":
            text = message.get("text")
            if not isinstance(text, str) or len(text) > 4000:
                return "invalid_messages"
        elif message.get("kind") == "media":
            url = message.get("media_url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                return "invalid_messages"
        else:
            return "invalid_messages"
    return None


def download_media(url: str, path: str, *, deadline=None) -> None:
    timeout = min(30, max(0.001, deadline - time.monotonic())) if deadline is not None else 30
    with urlopen(url, timeout=timeout) as response, open(path, "wb") as output:
        size = 0
        while chunk := response.read(64 * 1024):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("bundle timeout")
            size += len(chunk)
            if size > MAX_MEDIA_BYTES:
                raise ValueError("media_too_large")
            output.write(chunk)


def send_pic_messages(target, messages, subprocess_runner=None, downloader=None) -> tuple[bool, str]:
    error = validate_request(target, messages)
    if error:
        return False, error
    runner = subprocess_runner or subprocess.run
    download = downloader or download_media
    deadline = time.monotonic() + 100.0
    temp_dir = None
    try:
        for i, message in enumerate(messages, 1):
            if time.monotonic() >= deadline:
                return False, "bundle timeout"
            try:
                if message["kind"] == "media":
                    if temp_dir is None:
                        temp_dir = tempfile.mkdtemp(prefix="pic-send-")
                    extension = Path(urlsplit(message["media_url"]).path).suffix or ".jpg"
                    path = str(Path(temp_dir) / f"media-{i}{extension}")
                    if downloader is None:
                        download(message["media_url"], path, deadline=deadline)
                    else:
                        download(message["media_url"], path)
                    if Path(path).stat().st_size > MAX_MEDIA_BYTES:
                        raise ValueError("media_too_large")
                    text = f"MEDIA:{path}"
                else:
                    text = re.sub("MEDIA:", "MEDIA :", message["text"], flags=re.IGNORECASE)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, "bundle timeout"
                timeout = min(20.0, remaining)
                proc = runner(
                    hermes_command(["send", "-t", target, "-q", "--", text], timeout),
                    capture_output=True, text=True, timeout=timeout, check=False, shell=False,
                )
                if time.monotonic() >= deadline:
                    return False, "bundle timeout"
                if proc.returncode != 0:
                    error = proc.stderr or proc.stdout or f"exit code {proc.returncode}"
                    return False, f"message {i}: {sanitize_error(error)}"
            except FileNotFoundError:
                return False, f"message {i}: {sanitize_error('executable or media not found: ' + get_hermes_path())}"
            except Exception as exc:
                if time.monotonic() >= deadline:
                    return False, "bundle timeout"
                return False, f"message {i}: {sanitize_error(str(exc))}"
            if i < len(messages):
                time.sleep(min(1.5, max(0, deadline - time.monotonic())))
        return True, "sent"
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir)
