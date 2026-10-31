from unittest.mock import MagicMock, call, patch

from app.services.image_downloader import (
    NICHE_SEARCH_HINT,
    _collect_urls_9router,
    download_images,
)


def _sample_markdown(id_prefix: str, count: int) -> str:
    lines = []
    for i in range(count):
        lines.append(
            f"![Image {i} on August 09, 2026 in England.]"
            f"(https://media.gettyimages.com/id/{id_prefix}{i}/photo/p{i}.jpg?s=612x612&k=20)"
        )
    return "\n".join(lines)


def test_niche_search_hint_mapping():
    assert NICHE_SEARCH_HINT["F1"] == "Formula 1"
    assert NICHE_SEARCH_HINT["MotoGP"] == "MotoGP"
    assert NICHE_SEARCH_HINT["WorldSBK"] == "WorldSBK"
    assert NICHE_SEARCH_HINT["UFC"] == "UFC"
    assert NICHE_SEARCH_HINT["Boxing"] == "boxing"
    assert NICHE_SEARCH_HINT["NBA"] == "NBA"

    def map_hint(niche):
        return NICHE_SEARCH_HINT.get(niche or "", niche or None)

    assert map_hint("F1") == "Formula 1"
    assert map_hint("MotoGP") == "MotoGP"
    assert map_hint("WorldSBK") == "WorldSBK"
    assert map_hint("UFC") == "UFC"
    assert map_hint("Boxing") == "boxing"
    assert map_hint("NBA") == "NBA"
    assert map_hint("IndyCar") == "IndyCar"
    assert map_hint("WRC") == "WRC"
    assert map_hint(None) is None
    assert map_hint("") is None


def test_search_hint_added_to_first_and_topup_phrases():
    md_page1 = _sample_markdown("first", 2)
    md_topup = _sample_markdown("topup", 3)

    with patch("app.services.image_downloader._9router_fetch_markdown", side_effect=[md_page1, md_topup]) as mock_fetch:
        urls, _ = _collect_urls_9router("alex pereira", max_num=5, max_pages=1, allow_topup=True, search_hint="UFC")

    assert len(urls) == 5
    assert mock_fetch.call_count == 2

    first_call = mock_fetch.call_args_list[0]
    assert "phrase=alex%20pereira%20UFC" in first_call[0][0]
    assert first_call[1]["keyword"] == "alex pereira UFC"

    second_call = mock_fetch.call_args_list[1]
    assert "phrase=alex%20pereira%20UFC%20press" in second_call[0][0]
    assert second_call[1]["keyword"] == "alex pereira UFC press"


def test_no_search_hint_phrases_unchanged_regression():
    md_page1 = _sample_markdown("first", 2)
    md_topup = _sample_markdown("topup", 3)

    with patch("app.services.image_downloader._9router_fetch_markdown", side_effect=[md_page1, md_topup]) as mock_fetch:
        urls, _ = _collect_urls_9router("alex pereira", max_num=5, max_pages=1, allow_topup=True, search_hint=None)

    assert len(urls) == 5
    assert mock_fetch.call_count == 2

    first_call = mock_fetch.call_args_list[0]
    assert "phrase=alex%20pereira" in first_call[0][0]
    assert "UFC" not in first_call[0][0]
    assert first_call[1]["keyword"] == "alex pereira"

    second_call = mock_fetch.call_args_list[1]
    assert "phrase=alex%20pereira%20press" in second_call[0][0]
    assert second_call[1]["keyword"] == "alex pereira press"


def test_search_hint_already_in_keyword_not_duplicated():
    md_page1 = _sample_markdown("first", 2)
    md_topup = _sample_markdown("topup", 3)

    with patch("app.services.image_downloader._9router_fetch_markdown", side_effect=[md_page1, md_topup]) as mock_fetch:
        urls, _ = _collect_urls_9router("ufc fight night", max_num=5, max_pages=1, allow_topup=True, search_hint="UFC")

    assert len(urls) == 5
    assert mock_fetch.call_count == 2

    first_call = mock_fetch.call_args_list[0]
    assert "phrase=ufc%20fight%20night" in first_call[0][0]
    assert "ufc%20fight%20night%20UFC" not in first_call[0][0]
    assert first_call[1]["keyword"] == "ufc fight night"

    second_call = mock_fetch.call_args_list[1]
    assert "phrase=ufc%20fight%20night%20press" in second_call[0][0]
    assert second_call[1]["keyword"] == "ufc fight night press"


def test_search_hint_case_insensitive_match_not_duplicated():
    md_page1 = _sample_markdown("first", 5)

    with patch("app.services.image_downloader._9router_fetch_markdown", return_value=md_page1) as mock_fetch:
        urls, _ = _collect_urls_9router("Charles Leclerc Formula 1", max_num=5, allow_topup=True, search_hint="Formula 1")

    assert len(urls) == 5
    assert mock_fetch.call_count == 1
    call_args = mock_fetch.call_args_list[0]
    assert call_args[1]["keyword"] == "Charles Leclerc Formula 1"


def test_download_images_passes_search_hint_and_keeps_bare_keyword_as_vision_subject():
    fake_urls = ["https://media.gettyimages.com/id/999/photo/test.jpg"]
    fake_dates = {}

    with patch("app.services.image_downloader._collect_urls_flickr", return_value=([], {})), \
         patch("app.services.image_downloader._collect_urls_9router", return_value=(fake_urls, fake_dates)) as mock_collect, \
         patch("app.services.image_downloader._fetch_and_store", return_value=[]) as mock_store:

        download_images(
            keyword="alex pereira",
            dest_dir="/tmp/test_gallery",
            max_num=10,
            search_hint="UFC",
        )

    mock_collect.assert_called_once_with(
        "alex pereira", 20, frozenset(), None, True, search_hint="UFC",
    )
    mock_store.assert_called_once_with(
        fake_urls, "/tmp/test_gallery", 10, (500, 500), frozenset(), "9router",
        subject="alex pereira", captured_dates=fake_dates,
    )
