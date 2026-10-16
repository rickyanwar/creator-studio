from contextlib import contextmanager
import hashlib
import http.client
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
from typing import Generator
import urllib.parse
import urllib.request

from phone_runner.adb import AdbResult, run_adb
from phone_runner.config import (
    get_max_media_bytes,
    get_media_timeout,
    is_media_host_allowed,
)

CHUNK_SIZE = 64 * 1024
TAILSCALE_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


class ProhibitRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Rejects all HTTP redirect responses to prevent SSRF bypass."""

    def redirect_request(self, req: urllib.request.Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        raise ValueError(f"HTTP redirects prohibited: received status {code} to {newurl}")


def is_unsafe_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip_obj.is_loopback
        or ip_obj.is_private
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        or not ip_obj.is_global
        or (isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj in TAILSCALE_CGNAT_NET)
    )


class ValidatedMediaUrl(urllib.parse.SplitResult):
    """SplitResult carrying single pinned public IP chosen during URL validation."""

    pinned_ip: str

    def __new__(
        cls,
        split_result: urllib.parse.SplitResult,
        pinned_ip: str,
    ) -> "ValidatedMediaUrl":
        obj = super().__new__(
            cls,
            split_result.scheme,
            split_result.netloc,
            split_result.path,
            split_result.query,
            split_result.fragment,
        )
        obj.pinned_ip = pinned_ip
        return obj


class PinnedIPHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to validated IP; preserves hostname for SNI and Host header."""

    def __init__(
        self,
        hostname: str,
        pinned_ip: str,
        port: int = 443,
        **kwargs,
    ) -> None:
        super().__init__(host=hostname, port=port, **kwargs)
        self.pinned_ip = pinned_ip

    def connect(self) -> None:
        ip = ipaddress.ip_address(self.pinned_ip)
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if self.timeout is not None and self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(self.timeout)
            if self.source_address:
                sock.bind(self.source_address)
            sock.connect((self.pinned_ip, self.port))
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

            if self._tunnel_host:
                self._tunnel()

            server_hostname = self._tunnel_host or self.host
            self.sock = self._context.wrap_socket(
                sock,
                server_hostname=server_hostname,
            )
        except Exception:
            sock.close()
            raise


class PinnedIPHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPSHandler routing connections through PinnedIPHTTPSConnection."""

    def __init__(
        self,
        hostname: str,
        pinned_ip: str,
        port: int = 443,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.hostname = hostname
        self.pinned_ip = pinned_ip
        self.port = port

    def https_open(self, req: urllib.request.Request):
        def conn_factory(host: str, **conn_kwargs):
            # ponytail: pin single IP without fallback loop; add multi-IP retry if CDN failover required
            return PinnedIPHTTPSConnection(
                hostname=self.hostname,
                pinned_ip=self.pinned_ip,
                port=self.port,
                **conn_kwargs,
            )

        # No check_hostname kwarg: Python 3.12 removed HTTPSHandler._check_hostname (crashed live on the
        # VPS). Hostname + certificate checks still happen in PinnedIPHTTPSConnection.connect via
        # context.wrap_socket(server_hostname=...) with the default (check_hostname=True) context.
        return self.do_open(
            conn_factory,
            req,
            context=self._context,
        )


def validate_media_url(url: str) -> ValidatedMediaUrl:
    """Validates that URL uses HTTPS, is in allowed hosts, and resolves only to public IPs."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Media URL must be a non-empty string")

    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme.lower() != "https":
        raise ValueError(f"Only HTTPS media URLs are permitted; got scheme '{parsed.scheme}'")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in media URL are not permitted")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid port in media URL") from exc

    if port is not None and port != 443:
        raise ValueError(f"Explicit port {port} is not permitted; only HTTPS port 443 is allowed")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Media URL is missing hostname")

    if not is_media_host_allowed(hostname):
        raise ValueError(f"Media host '{hostname}' is not in allowlist (PHONE_RUNNER_MEDIA_HOSTS)")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if is_unsafe_ip(literal_ip):
            raise ValueError(f"Prohibited IP address '{literal_ip}' for media host '{hostname}'")
        pinned_ip = str(literal_ip)
    else:
        try:
            addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        except Exception as exc:
            raise ValueError(f"DNS resolution failed for media host '{hostname}'") from exc

        if not addr_info:
            raise ValueError(f"No IP addresses resolved for media host '{hostname}'")

        validated_ips: list[str] = []
        for entry in addr_info:
            ip_str = entry[4][0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError as exc:
                raise ValueError(f"Invalid resolved IP for media host '{hostname}'") from exc

            if is_unsafe_ip(ip_obj):
                raise ValueError(f"Prohibited IP address '{ip_obj}' resolved for media host '{hostname}'")
            validated_ips.append(ip_str)

        if not validated_ips:
            raise ValueError(f"No valid global IP addresses resolved for media host '{hostname}'")

        pinned_ip = validated_ips[0]

    return ValidatedMediaUrl(parsed, pinned_ip)


def download_media_file(
    url: str,
    dest_dir: str,
    max_bytes: int | None = None,
    timeout: float | None = None,
) -> tuple[str, str, int]:
    """Downloads a validated HTTPS media URL safely with no redirects and size bounds.

    Returns:
        (local_filepath, sha256_hex, byte_count)
    """
    validated = validate_media_url(url)
    hostname = validated.hostname
    if not hostname:
        raise ValueError("Media URL is missing hostname")
    pinned_ip = validated.pinned_ip
    port = validated.port or 443

    limit = max_bytes if max_bytes is not None else get_max_media_bytes()
    t = timeout if timeout is not None else get_media_timeout()

    handler = PinnedIPHTTPSHandler(hostname=hostname, pinned_ip=pinned_ip, port=port)
    # ponytail: empty proxy handler ignores ambient HTTP(S)_PROXY; add explicit proxy config if egress proxy required
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        ProhibitRedirectHandler(),
        handler,
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PhoneRunner/1.0", "Accept": "*/*"},
    )

    url_path = validated.path
    ext = Path(url_path).suffix.lower()
    if not ext or len(ext) > 6 or not all(c.isalnum() for c in ext[1:]):
        ext = ".bin"

    temp_fd, temp_path = tempfile.mkstemp(dir=dest_dir, prefix="stage_", suffix=ext)
    hasher = hashlib.sha256()
    total_bytes = 0

    try:
        with os.fdopen(temp_fd, "wb") as f, opener.open(req, timeout=t) as resp:
            # Check Content-Length if supplied
            content_length_header = resp.headers.get("Content-Length")
            if content_length_header:
                try:
                    declared_len = int(content_length_header)
                    if declared_len > limit:
                        raise ValueError(
                            f"Media Content-Length {declared_len} exceeds max size {limit} bytes"
                        )
                except ValueError as err:
                    if "exceeds max size" in str(err):
                        raise

            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > limit:
                    raise ValueError(f"Media download exceeded size limit of {limit} bytes")
                hasher.update(chunk)
                f.write(chunk)

        return temp_path, hasher.hexdigest(), total_bytes
    except Exception:
        if os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def push_media_to_device(
    serial: str,
    local_path: str,
    remote_dir: str = "/sdcard/Pictures/staging",
) -> str:
    """Pushes a local staged file to Android storage and notifies MediaStore.

    Returns the absolute path on the device.
    """
    filename = Path(local_path).name
    remote_path = f"{remote_dir}/{filename}"

    # Ensure target directory exists without shell interpolation
    mkdir_res = run_adb(["shell", "mkdir", "-p", remote_dir], serial=serial)
    if mkdir_res.returncode != 0:
        raise RuntimeError(f"Failed to create remote staging directory (exit code {mkdir_res.returncode})")

    push_res = run_adb(["push", local_path, remote_path], serial=serial)
    if push_res.returncode != 0:
        raise RuntimeError(f"ADB push failed (exit code {push_res.returncode})")

    # Notify MediaStore to index new file
    run_adb(
        [
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            f"file://{remote_path}",
        ],
        serial=serial,
    )

    return remote_path


@contextmanager
def stage_media_files(
    media_urls: list[str],
    serial: str,
    cleanup_remote: bool = True,
) -> Generator[list[dict[str, object]], None, None]:
    """Context manager for staging media locally and onto device with guaranteed cleanup.

    Yields a list of dicts:
        {"url": str, "local_path": str, "remote_path": str, "sha256": str, "bytes": int}
    """
    temp_dir = tempfile.mkdtemp(prefix="phone_runner_staging_")
    staged: list[dict[str, object]] = []
    remote_paths: list[str] = []

    try:
        for u in media_urls:
            local_p, sha, bcount = download_media_file(u, dest_dir=temp_dir)
            remote_p = push_media_to_device(serial, local_p)
            remote_paths.append(remote_p)
            staged.append({
                "url": u,
                "local_path": local_p,
                "remote_path": remote_p,
                "sha256": sha,
                "bytes": bcount,
            })
        yield staged
    finally:
        # Clean local temporary directory
        shutil.rmtree(temp_dir, ignore_errors=True)

        # Clean remote files if requested
        if cleanup_remote and remote_paths:
            for rp in remote_paths:
                run_adb(["shell", "rm", "-f", rp], serial=serial)
