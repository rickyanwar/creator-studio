"""share_url_matches_expected_id: a copied profile share link that redirects to a username must map to the expected id."""
from unittest import mock

from phone_runner.resolver import share_url_matches_expected_id


class _Resp:
    def __init__(self, status, location):
        self.status = status
        self.headers = {"Location": location}

    def close(self):
        pass


def _opener(mapping):
    op = mock.MagicMock()

    def _open(req, timeout=None):
        return mapping[req.full_url]

    op.open.side_effect = _open
    return op


SHARE = "https://www.facebook.com/share/1E4hTTVmBU/"
EXPECTED = "https://www.facebook.com/profile.php?id=100009439229483"


def test_same_vanity_proves_the_expected_profile():
    op = _opener({
        SHARE: _Resp(302, "https://www.facebook.com/afnindarmegayuntara?rdid=x&share_url=y"),
        EXPECTED: _Resp(302, "https://www.facebook.com/afnindarmegayuntara/"),
    })
    assert share_url_matches_expected_id(SHARE, "100009439229483", opener=op) is True


def test_different_vanity_is_rejected():
    op = _opener({
        SHARE: _Resp(302, "https://www.facebook.com/someoneelse?rdid=x"),
        EXPECTED: _Resp(302, "https://www.facebook.com/afnindarmegayuntara/"),
    })
    assert share_url_matches_expected_id(SHARE, "100009439229483", opener=op) is False


def test_login_wall_or_foreign_host_is_rejected():
    op = _opener({
        SHARE: _Resp(302, "https://www.facebook.com/login/?next=x"),
        EXPECTED: _Resp(302, "https://www.facebook.com/afnindarmegayuntara/"),
    })
    assert share_url_matches_expected_id(SHARE, "100009439229483", opener=op) is False
    op2 = _opener({SHARE: _Resp(302, "https://evil.example/afnindarmegayuntara")})
    assert share_url_matches_expected_id(SHARE, "100009439229483", opener=op2) is False


def test_non_share_url_or_bad_id_never_calls_network():
    op = _opener({})
    assert share_url_matches_expected_id("https://www.facebook.com/profile.php?id=1", "100009439229483", opener=op) is False
    assert share_url_matches_expected_id(SHARE, "not-a-number", opener=op) is False
    op.open.assert_not_called()
