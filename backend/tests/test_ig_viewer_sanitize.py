from app.services.ig_viewer_sanitize import sanitize_error


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
