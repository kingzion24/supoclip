import sys
from types import SimpleNamespace

import httpx

from src import discover, trends

CURRENT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>Simba vs Yanga</title><ht:approx_traffic>20000+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>Derby ends in a draw</ht:news_item_title></ht:news_item></item>
<item><title>Diamond Platnumz</title><ht:approx_traffic>5000+</ht:approx_traffic></item>
</channel></rss>"""

OLD_FEED = """<rss xmlns:ht="https://trends.google.com/trends/trendingsearches/daily"><channel>
<item><title>Marioo</title><ht:approx_traffic>2,000+</ht:approx_traffic></item>
</channel></rss>"""


def test_parse_google_trends_rss_handles_both_feed_formats():
    assert trends.parse_google_trends_rss(CURRENT_FEED) == [
        {"title": "Simba vs Yanga", "traffic": "20000+", "news": ["Derby ends in a draw"]},
        {"title": "Diamond Platnumz", "traffic": "5000+", "news": []},
    ]
    assert trends.parse_google_trends_rss(OLD_FEED)[0]["title"] == "Marioo"
    assert trends.parse_google_trends_rss("not xml") == []


def test_get_trends_combines_sources_and_caches(monkeypatch):
    trends._cache.clear()
    config = SimpleNamespace(trends_region="TZ", resolve_youtube_data_api_key=lambda: "yt-key")
    monkeypatch.setattr(trends, "get_config", lambda: config)
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(url)
        request = httpx.Request("GET", url)
        if "trends.google.com" in url:
            assert params["geo"] == "TZ"
            return httpx.Response(200, text=CURRENT_FEED, request=request)
        assert params["regionCode"] == "TZ" and params["chart"] == "mostPopular"
        return httpx.Response(200, request=request, json={"items": [
            {"id": "abc", "snippet": {"title": "Bongo hit", "channelTitle": "Wasafi"},
             "statistics": {"viewCount": "1200"}},
        ]})

    monkeypatch.setattr(trends.httpx, "get", fake_get)
    data = trends.get_trends()
    assert data["region_name"] == "Tanzania"
    assert [s["title"] for s in data["searches"]] == ["Simba vs Yanga", "Diamond Platnumz"]
    assert data["videos"][0]["views"] == 1200
    assert trends.get_trends() is data and len(calls) == 2  # served from cache

    signals = trends.format_trend_signals(data)
    assert "Trending right now in Tanzania" in signals
    assert "- search: Simba vs Yanga [20000+ searches] (Derby ends in a draw)" in signals
    assert "- popular video: Bongo hit (Wasafi)" in signals


def test_get_trends_is_soft_when_offline_and_can_be_disabled(monkeypatch):
    trends._cache.clear()
    config = SimpleNamespace(trends_region="TZ", resolve_youtube_data_api_key=lambda: None)
    monkeypatch.setattr(trends, "get_config", lambda: config)

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(trends.httpx, "get", offline)
    data = trends.get_trends()
    assert data["searches"] == [] and data["videos"] == []
    assert trends.format_trend_signals(data) == ""
    config.trends_region = ""
    assert trends.get_trends()["region"] == ""


def test_search_youtube_drops_livestreams_and_short_videos(monkeypatch):
    entries = [
        {"id": "a1", "title": "Long podcast", "channel": "Mic", "duration": 3600, "view_count": 10},
        {"id": "b2", "title": "Live now", "duration": None, "live_status": "is_live"},
        {"id": "c3", "title": "Short clip", "uploader": "X", "duration": 45},
        {"id": "d4", "title": "Channel result", "ie_key": "YoutubeTab"},
    ]
    seen = {}

    class FakeYDL:
        def __init__(self, options):
            seen["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, query, download):
            seen["query"] = query
            return {"entries": entries}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    results = discover.search_youtube("  biashara  tanzania ", limit=5, podcasts=True, min_minutes=10)
    assert seen["query"].endswith(":biashara tanzania podcast")
    assert seen["options"]["extract_flat"] == "in_playlist"
    assert [r["id"] for r in results] == ["a1"]
    assert results[0]["url"] == "https://www.youtube.com/watch?v=a1"
    assert results[0]["thumbnail"].endswith("/a1/hqdefault.jpg")
