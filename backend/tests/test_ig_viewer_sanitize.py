from app.services.ig_viewer_sanitize import sanitize_error, describe_failure, describe_success


def test_sanitize_error_redacts_secrets_urls_controls_and_caps():
    text = ("\x1bBearer my.secret\n github_pat_abcdef ghp_abc123 ghx_random sk-secret "
            "https://alice:password@example.com/path?token=hidden#fragment")
    result = sanitize_error(text)
    assert "https://example.com/path" in result
    assert all(secret not in result for secret in
               ("\x1b", "\n", "my.secret", "github_pat_", "ghp_", "ghx_", "sk-secret",
                "alice", "password", "hidden", "fragment"))
    assert sanitize_error("x" * 500) == "x" * 300
    assert sanitize_error("abcdef", limit=3) == "abc"

def test_describe_failure():
    assert describe_failure("script", "no posts captured") == "Page loaded but no posts were returned (account may be private, renamed, deleted or have no posts)"
    assert describe_failure("script", "posts clicked=False") == "Posts tab did not appear on the viewer page"
    assert describe_failure("script", "not found or private (.alert)") == "Viewer says the account was not found or is private"
    assert describe_failure("script", "parse failed") == "Viewer returned data in an unexpected format (our parser may need an update)"
    assert describe_failure("script", "some other error") == "The viewer page changed format or did not load completely"
    assert describe_failure("blocked", "anything") == "Cloudflare check was not passed"
    assert describe_failure("site_down", "timeout error") == "The viewer site did not respond in time (site slow or server busy)"
    assert describe_failure("site_down", "HTTP 502") == "The viewer site is unreachable (HTTP 5xx / network)"
    assert describe_failure("unknown", "error") == "An unknown error occurred"
    assert describe_failure(None, None) == "An unknown error occurred"

def test_describe_success():
    assert describe_success(None) == "OK"
    assert describe_success(5) == "OK — 5 posts"
    assert describe_success(0) == "OK — 0 posts"
