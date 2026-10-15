import io
import socket
from unittest import mock
import urllib.error
import urllib.request

import pytest

from phone_runner.adb import AdbResult
from phone_runner.media_staging import (
    PinnedIPHTTPSConnection,
    PinnedIPHTTPSHandler,
    ProhibitRedirectHandler,
    download_media_file,
    push_media_to_device,
    stage_media_files,
    validate_media_url,
)

TEST_SERIAL = "100.64.96.50:5555"
TEST_HOST = "cdn.example.com"
TEST_URL = f"https://{TEST_HOST}/images/photo1.jpg"


@pytest.fixture(autouse=True)
def media_env(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", TEST_HOST)
    monkeypatch.setenv("PHONE_RUNNER_MAX_MEDIA_BYTES", "1048576")  # 1 MB
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_TIMEOUT_SEC", "5.0")


def test_reject_non_https():
    with pytest.raises(ValueError, match="Only HTTPS media URLs are permitted"):
        validate_media_url(f"http://{TEST_HOST}/pic.png")

    with pytest.raises(ValueError, match="Only HTTPS media URLs are permitted"):
        validate_media_url("file:///etc/passwd")


def test_reject_unauthorized_host():
    with pytest.raises(ValueError, match="not in allowlist"):
        validate_media_url("https://malicious.evil.com/pic.png")


def test_reject_private_and_loopback_ips(monkeypatch):
    prohibited_ips = [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.100",
        "172.16.0.5",
        "169.254.169.254",  # AWS/cloud metadata
        "::1",
        "fe80::1",
    ]

    for bad_ip in prohibited_ips:
        with mock.patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (bad_ip, 443))]
            with pytest.raises(ValueError, match="Prohibited IP address"):
                validate_media_url(TEST_URL)


def test_prohibit_redirect_handler():
    handler = ProhibitRedirectHandler()
    req = mock.MagicMock()
    with pytest.raises(ValueError, match="HTTP redirects prohibited: received status 302"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://cdn.example.com/other.jpg")


def test_reject_content_length_too_large(tmp_path):
    mock_dns_return = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    mock_resp = mock.MagicMock()
    mock_resp.headers = {"Content-Length": "2097152"}  # 2 MB > 1 MB limit
    mock_resp.__enter__.return_value = mock_resp

    with (
        mock.patch("socket.getaddrinfo", return_value=mock_dns_return),
        mock.patch("urllib.request.OpenerDirector.open", return_value=mock_resp),
    ):
        with pytest.raises(ValueError, match="exceeds max size"):
            download_media_file(TEST_URL, dest_dir=str(tmp_path))


def test_reject_stream_exceeding_max_bytes(tmp_path):
    mock_dns_return = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    mock_resp = mock.MagicMock()
    mock_resp.headers = {}
    # Yield chunks exceeding 1 MB
    mock_resp.read.side_effect = [b"a" * 600000, b"b" * 600000]
    mock_resp.__enter__.return_value = mock_resp

    with (
        mock.patch("socket.getaddrinfo", return_value=mock_dns_return),
        mock.patch("urllib.request.OpenerDirector.open", return_value=mock_resp),
    ):
        with pytest.raises(ValueError, match="Media download exceeded size limit"):
            download_media_file(TEST_URL, dest_dir=str(tmp_path))


def test_successful_download_and_adb_push(tmp_path):
    mock_dns_return = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    payload_data = b"fake-jpg-binary-content-12345"
    mock_resp = mock.MagicMock()
    mock_resp.headers = {"Content-Length": str(len(payload_data))}
    mock_resp.read.side_effect = [payload_data, b""]
    mock_resp.__enter__.return_value = mock_resp

    with (
        mock.patch("socket.getaddrinfo", return_value=mock_dns_return),
        mock.patch("urllib.request.OpenerDirector.open", return_value=mock_resp),
        mock.patch("phone_runner.media_staging.run_adb") as mock_adb,
    ):
        mock_adb.return_value = AdbResult(0, "OK", "")
        with stage_media_files([TEST_URL], serial=TEST_SERIAL) as staged:
            assert len(staged) == 1
            item = staged[0]
            assert item["url"] == TEST_URL
            assert item["bytes"] == len(payload_data)
            assert item["remote_path"].startswith("/sdcard/Pictures/staging/")

        # Verify ADB push called with discrete argv (no shell interpolation)
        push_call = next(c for c in mock_adb.call_args_list if c[0][0][0] == "push")
        assert push_call[0][0][0] == "push"
        assert push_call[1]["serial"] == TEST_SERIAL


def test_reject_explicit_port_8765():
    with pytest.raises(ValueError, match="Explicit port 8765 is not permitted"):
        validate_media_url(f"https://{TEST_HOST}:8765/pic.png")


def test_accept_implicit_and_explicit_443():
    mock_dns_return = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with mock.patch("socket.getaddrinfo", return_value=mock_dns_return):
        v_implicit = validate_media_url(f"https://{TEST_HOST}/pic.png")
        assert v_implicit.port is None
        assert v_implicit.pinned_ip == "93.184.216.34"

        v_explicit = validate_media_url(f"https://{TEST_HOST}:443/pic.png")
        assert v_explicit.port == 443
        assert v_explicit.pinned_ip == "93.184.216.34"


def test_reject_tailscale_cgnat_ips():
    for bad_ip in ["100.64.0.1", "100.100.50.2", "100.127.255.254"]:
        with mock.patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (bad_ip, 443))]
            with pytest.raises(ValueError, match="Prohibited IP address"):
                validate_media_url(TEST_URL)


def test_reject_credentials_in_url():
    with pytest.raises(ValueError, match="Credentials in media URL are not permitted"):
        validate_media_url(f"https://user:pass@{TEST_HOST}/pic.png")
    with pytest.raises(ValueError, match="Credentials in media URL are not permitted"):
        validate_media_url(f"https://user@{TEST_HOST}/pic.png")


def test_dns_rebind_simulation_resolves_once_and_connects_to_first_public_ip(tmp_path):
    mock_dns = mock.MagicMock(
        side_effect=[
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ]
    )
    mock_raw_sock = mock.MagicMock()
    mock_ssl_sock = mock.MagicMock()
    mock_ssl_sock.makefile.return_value = io.BytesIO(
        b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ntest"
    )
    mock_ctx = mock.MagicMock()
    mock_ctx.wrap_socket.return_value = mock_ssl_sock

    with (
        mock.patch("socket.getaddrinfo", mock_dns),
        mock.patch("socket.socket", return_value=mock_raw_sock),
        mock.patch("ssl._create_default_https_context", return_value=mock_ctx),
    ):
        local_path, sha, bcount = download_media_file(TEST_URL, dest_dir=str(tmp_path))

    assert mock_dns.call_count == 1
    mock_raw_sock.connect.assert_called_once_with(("93.184.216.34", 443))
    assert bcount == 4


def test_tls_sni_and_http_host_header():
    mock_raw_sock = mock.MagicMock()
    mock_ssl_sock = mock.MagicMock()
    mock_ctx = mock.MagicMock()
    mock_ctx.wrap_socket.return_value = mock_ssl_sock

    conn = PinnedIPHTTPSConnection(
        hostname=TEST_HOST,
        pinned_ip="93.184.216.34",
        port=443,
        context=mock_ctx,
    )

    with mock.patch("socket.socket", return_value=mock_raw_sock):
        conn.connect()

    mock_raw_sock.connect.assert_called_once_with(("93.184.216.34", 443))
    mock_ctx.wrap_socket.assert_called_once_with(mock_raw_sock, server_hostname=TEST_HOST)

    conn.request("GET", "/images/photo1.jpg")
    sent_bytes = b"".join(call.args[0] for call in mock_ssl_sock.sendall.call_args_list)
    assert f"Host: {TEST_HOST}".encode() in sent_bytes


def test_reject_mixed_unsafe_dns_answers():
    mock_dns_return = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
    ]
    with mock.patch("socket.getaddrinfo", return_value=mock_dns_return):
        with pytest.raises(ValueError, match="Prohibited IP address"):
            validate_media_url(TEST_URL)


def test_download_media_timeout(tmp_path):
    mock_dns_return = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with (
        mock.patch("socket.getaddrinfo", return_value=mock_dns_return),
        mock.patch(
            "urllib.request.OpenerDirector.open",
            side_effect=urllib.error.URLError(socket.timeout("timed out")),
        ),
    ):
        with pytest.raises(urllib.error.URLError):
            download_media_file(TEST_URL, dest_dir=str(tmp_path), timeout=0.01)


def test_ambient_proxy_ignored_and_pinned_connection_preserved(tmp_path, monkeypatch):
    fake_proxy = "http://fake-proxy.corp.internal:8080"
    monkeypatch.setenv("https_proxy", fake_proxy)
    monkeypatch.setenv("HTTPS_PROXY", fake_proxy)

    with mock.patch("urllib.request.getproxies", return_value={"https": fake_proxy}):
        empty_proxy_handler = urllib.request.ProxyHandler({})
        pinned_handler = PinnedIPHTTPSHandler(hostname=TEST_HOST, pinned_ip="93.184.216.34", port=443)
        opener = urllib.request.build_opener(
            empty_proxy_handler,
            ProhibitRedirectHandler(),
            pinned_handler,
        )

        assert empty_proxy_handler.proxies == {}
        https_handlers = opener.handle_open.get("https", [])
        assert https_handlers == [pinned_handler]
        assert not any(isinstance(h, urllib.request.ProxyHandler) for h in https_handlers)

        mock_dns = mock.MagicMock(
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        )
        mock_raw_sock = mock.MagicMock()
        mock_ssl_sock = mock.MagicMock()
        mock_ssl_sock.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ntest"
        )
        mock_ctx = mock.MagicMock()
        mock_ctx.wrap_socket.return_value = mock_ssl_sock

        with (
            mock.patch("socket.getaddrinfo", mock_dns),
            mock.patch("socket.socket", return_value=mock_raw_sock),
            mock.patch("ssl._create_default_https_context", return_value=mock_ctx),
        ):
            local_path, sha, bcount = download_media_file(TEST_URL, dest_dir=str(tmp_path))

        assert mock_dns.call_count == 1
        assert mock_dns.call_args == mock.call(TEST_HOST, 443, proto=socket.IPPROTO_TCP)
        mock_raw_sock.connect.assert_called_once_with(("93.184.216.34", 443))
        mock_ctx.wrap_socket.assert_called_once_with(mock_raw_sock, server_hostname=TEST_HOST)
        assert bcount == 4
