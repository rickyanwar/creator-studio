import pytest


def test_scrapling_fetchers_import():
    pytest.importorskip("scrapling")
    from scrapling.fetchers import Fetcher

    assert Fetcher is not None
