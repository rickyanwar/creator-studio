import pytest
import httpx
from app.services.repliz_client import ReplizClient, ReplizPlanRequired


def test_repliz_get_content_statistic_200():
    def mock_handler(request):
        assert request.url.path == "/public/content/123_456/statistic"
        assert request.url.query.decode() == "accountId=acc1"
        return httpx.Response(200, json={"data": {"likes": 10}})

    transport = httpx.MockTransport(mock_handler)
    client = ReplizClient("ak", "sk", base_url="https://api.repliz.com")
    client._client = httpx.Client(base_url=client.base_url, headers=client._client.headers, transport=transport)

    res = client.get_content_statistic("123_456", "acc1")
    assert res == {"data": {"likes": 10}}


def test_repliz_get_content_statistic_402():
    def mock_handler(request):
        return httpx.Response(402, text="Upgrade your plan")

    transport = httpx.MockTransport(mock_handler)
    client = ReplizClient("ak", "sk", base_url="https://api.repliz.com")
    client._client = httpx.Client(base_url=client.base_url, headers=client._client.headers, transport=transport)

    with pytest.raises(ReplizPlanRequired, match="Upgrade your plan"):
        client.get_content_statistic("123_456", "acc1")


def test_repliz_get_content_statistic_500():
    def mock_handler(request):
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(mock_handler)
    client = ReplizClient("ak", "sk", base_url="https://api.repliz.com")
    client._client = httpx.Client(base_url=client.base_url, headers=client._client.headers, transport=transport)

    with pytest.raises(httpx.HTTPStatusError):
        client.get_content_statistic("123_456", "acc1")
