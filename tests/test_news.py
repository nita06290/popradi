import pytest
from datetime import timedelta
from email.utils import format_datetime

from radio.news import (
    canonical_url,
    collect,
    dedupe,
    google_news_url,
    normalize_title,
    parse_rss,
    select_articles,
    within_window,
)
from tests.conftest import make_article


def rss(items, now):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><pubDate>{format_datetime(now - timedelta(hours=h))}</pubDate>"
        f"<description>{d}</description><source url='https://x'>{s}</source></item>"
        for t, u, h, d, s in items
    )
    return f"<?xml version='1.0' encoding='UTF-8'?><rss><channel>{body}</channel></rss>"


def test_google_news_url_is_japanese_and_one_day():
    u = google_news_url("生成AI")
    assert "hl=ja" in u and "gl=JP" in u and "ceid=JP:ja" in u
    assert "when%3A1d" in u


def test_parse_rss_strips_source_suffix_and_html(now):
    xml = rss([("見出しです - テスト新聞", "https://a.example/1", 1, "&lt;b&gt;説明&lt;/b&gt;本文", "テスト新聞")], now)
    [a] = parse_rss(xml)
    assert a.title == "見出しです"
    assert a.source == "テスト新聞"
    assert a.description == "説明 本文"
    assert a.published_at.utcoffset() == timedelta(hours=9)


def test_parse_rss_drops_description_that_repeats_title(now):
    xml = rss([("同じ見出し - 媒体", "https://a.example/1", 1, "同じ見出し", "媒体")], now)
    assert parse_rss(xml)[0].description == ""


def test_parse_rss_broken_xml_returns_empty():
    assert parse_rss("<rss><channel><item>") == []


def test_parse_atom(now):
    xml = (
        "<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Atom記事</title>"
        "<link href='https://a.example/atom'/><updated>2026-11-08T03:00:00+09:00</updated>"
        "<summary>要約</summary></entry></feed>"
    )
    [a] = parse_rss(xml, default_source="Atom媒体")
    assert a.url == "https://a.example/atom" and a.source == "Atom媒体"


def test_canonical_url_removes_tracking():
    assert canonical_url("https://www.Example.com/a/?utm_source=x&id=3#top") == "https://example.com/a?id=3"


def test_normalize_title_ignores_punctuation_and_width():
    assert normalize_title("ＡＩ「新製品」発表！ - 媒体", "媒体") == normalize_title("AI 新製品 発表")


def test_within_window(now):
    assert within_window(make_article("a", hours_ago=29), now, 30)
    assert not within_window(make_article("b", hours_ago=31), now, 30)
    assert not within_window(make_article("future", hours_ago=-2), now, 30)


def test_dedupe_by_url_and_similar_title():
    a1 = make_article("ミライ技研、生成AIアシスタントを全社導入", url="https://a.example/1?utm_source=rss")
    a2 = make_article("ミライ技研、生成AIアシスタントを全社に導入", url="https://b.example/2")
    a3 = make_article("別のニュース", url="https://a.example/1")
    a4 = make_article("全然ちがう話題です", url="https://c.example/3")
    out = dedupe([a1, a2, a3, a4])
    assert [a.title for a in out] == [a1.title, a4.title]


def test_dedupe_respects_previously_broadcast_urls():
    a = make_article("昨日のニュース", url="https://a.example/old")
    assert dedupe([a], seen_urls={"https://a.example/old"}) == []


def test_select_articles_limits_and_round_robin(now):
    topics = [{"name": "A", "keywords": ["AI"]}, {"name": "B", "keywords": ["クラウド"]}]
    by_topic = {
        "A": [make_article(t, hours_ago=i + 1, topic="") for i, t in enumerate(
            ["AI翻訳アプリが方言に対応", "病院でAI問診の実証実験", "AIで農作物の収穫時期を予測", "自治体がAIチャットを導入", "AI作曲コンテストの結果発表"])],
        "B": [make_article(t, hours_ago=i + 1, topic="") for i, t in enumerate(
            ["クラウド料金の値下げ発表", "銀行がクラウドへ全面移行", "クラウド障害で決済が停止", "中小企業向けクラウド補助金", "クラウドゲームの新サービス"])],
    }
    out = select_articles(by_topic, topics, now, max_per_topic=2, max_total=3)
    assert len(out) == 3
    assert [a.topic for a in out] == ["A", "B", "A"]
    assert all(a.id.startswith("n") for a in out)


def test_select_articles_requires_keyword_hit_and_window(now):
    topics = [{"name": "A", "keywords": ["生成AI"]}]
    by_topic = {"A": [make_article("関係ない話", topic=""), make_article("生成AIの古い話", hours_ago=50, topic="")]}
    assert select_articles(by_topic, topics, now) == []


def test_collect_survives_failing_feeds(now):
    cfg = {
        "topics": [{"name": "A", "keywords": ["生成AI", "LLM"]}],
        "extra_feeds": [{"name": "X", "url": "https://broken.example/rss"}],
    }
    good = rss([("生成AIの新サービス - 媒体", "https://a.example/ok", 2, "説明", "媒体")], now)

    def fetch(url):
        if "LLM" in url or "broken" in url:
            raise ConnectionError("down")
        return good

    logs = []
    out = collect(cfg, now, fetch, log=logs.append)
    assert [a.title for a in out] == ["生成AIの新サービス"]
    assert len(logs) == 2


# ---------------------------------------------------------------- 実データ（Google News）で見つかった問題の回帰テスト

from radio.news import clean_title, in_region, relevance_terms  # noqa: E402


def test_google_news_description_repeating_title_is_dropped(now):
    # 実際の Google News の description は「<a>見出し</a>&nbsp;<font>媒体名</font>」
    desc = "&lt;a href='https://x'&gt;日本、決勝で敗れ準優勝 サッカー&lt;/a&gt;&amp;nbsp;&lt;font&gt;博多経済新聞&lt;/font&gt;"
    xml = rss([("日本、決勝で敗れ準優勝 サッカー - 博多経済新聞", "https://a.example/1", 1, desc, "博多経済新聞")], now)
    assert parse_rss(xml)[0].description == ""


def test_real_description_with_new_info_is_kept(now):
    xml = rss([("新展覧会が開幕 - 媒体", "https://a.example/1", 1, "上野で来年1月まで開催。入場は無料。", "媒体")], now)
    assert parse_rss(xml)[0].description == "上野で来年1月まで開催。入場は無料。"


@pytest.mark.parametrize("raw,clean", [
    ("＜画像1 / 3＞ホテルの部屋が会場!?", "ホテルの部屋が会場!?"),
    ("画像1 / 2＞【連載】東京都写真美術館で写真展", "【連載】東京都写真美術館で写真展"),
    ("巡りたいアートスポット（都内）｜Page 3", "巡りたいアートスポット（都内）"),
    ("写真2/3｜東京・大阪で展覧会「エッシャー 無限のつくりかた」", "東京・大阪で展覧会「エッシャー 無限のつくりかた」"),
    ("普通の見出し", "普通の見出し"),
])
def test_clean_title(raw, clean):
    assert clean_title(raw) == clean


ART = {
    "name": "美術", "keywords": ["美術展 東京", "展覧会 東京", "アートイベント 東京"],
    "region_any": ["東京", "上野", "国立新美術館"], "exclude_any": ["京都", "金沢"],
}


def test_relevance_terms_split_multiword_and_drop_region():
    assert relevance_terms(ART) == ["美術展", "展覧会", "アートイベント"]


@pytest.mark.parametrize("title,ok", [
    ("国立新美術館「ディーヴァ展」の詳細が発表", True),       # 東京の語が無くても館名で通す
    ("上野の森美術館で来年1月から", True),
    ("京都の国際アートフェアが開幕", False),
    ("金沢〈国立工芸館〉で名作展", False),
    ("京都と東京で巡回展", True),                            # 見出しに東京があれば通す
    ("愛媛・タオル美術館が展覧会", False),
])
def test_in_region(title, ok):
    assert in_region(make_article(title), ART) is ok


def test_select_drops_off_region_and_unrelated(now):
    topics = [ART]
    by_topic = {"美術": [
        make_article("ゲーム30周年展覧会、上野の森美術館で", topic=""),
        make_article("京都で美術展が開幕", topic=""),
        make_article("ポルシェが東京・丸の内に降臨", topic=""),  # 地域は合うが美術と無関係
    ]}
    out = select_articles(by_topic, topics, now, max_per_topic=5, max_total=5)
    assert [a.title for a in out] == ["ゲーム30周年展覧会、上野の森美術館で"]


# 2026-11-10 の実データ: 同じ報道写真展が媒体違いで3本入った
SAME_EVENT = [
    "日中台100年を紹介 東京・汐留で報道写真展",
    "東京都港区で始まった報道写真展「日中台：交錯するまなざし―写真が語る100年」",
    "日中台１００年紹介、報道写真展 東京・汐留シオサイト",
]
DIFFERENT_EVENTS = [
    "【ボクらの維新通信社 連載】東京都写真美術館で幕末から明治の貴重な写真展を開催中!",
    "マツモトダイスケ写真展「奴等の気配と拾った石」が東京で開催",
    "“ナンセンス絵本”長新太の企画展が、ちひろ美術館・東京で開催",
    "国立新美術館「ディーヴァ展」の詳細が発表",
    "架空の画家“ユアサヱボシ”に注目！展覧会、東京・練馬区立美術館で開催中",
]


def test_same_event_from_different_outlets_is_duplicate():
    from itertools import combinations

    from radio.news import is_similar

    n = [normalize_title(t) for t in SAME_EVENT]
    assert all(is_similar(a, b) for a, b in combinations(n, 2))


def test_different_exhibitions_in_tokyo_are_not_duplicates():
    from itertools import combinations

    from radio.news import is_similar

    n = [normalize_title(t) for t in DIFFERENT_EVENTS + SAME_EVENT[:1]]
    bad = [(a, b) for a, b in combinations(n, 2) if is_similar(a, b)]
    assert bad == []


def test_dedupe_keeps_one_of_same_event():
    arts = [make_article(t, url=f"https://ex.example/{i}") for i, t in enumerate(SAME_EVENT + DIFFERENT_EVENTS[:2])]
    assert len(dedupe(arts)) == 3


def test_kyoto_museum_not_matched_by_tokyo_museum_name():
    """2026-11-10 の実データ: 「京都国立近代美術館」が東京の館名に一致して混入した。"""
    import json
    from pathlib import Path

    cfg = json.loads((Path(__file__).parent.parent / "config/topics.json").read_text(encoding="utf-8"))
    topic = next(t for t in cfg["topics"] if t.get("region_any"))
    kyoto = make_article("「ジュエリーは、誰を夢みる」（京都国立近代美術館）")
    takebashi = make_article("東京国立近代美術館で新しいコレクション展")
    assert not in_region(kyoto, topic)
    assert in_region(takebashi, topic)


def test_per_topic_max_overrides_default(now):
    topics = [{"name": "A", "keywords": ["AI"], "max": 3}, {"name": "B", "keywords": ["クラウド"], "max": 1}]
    by_topic = {
        "A": [make_article(t, topic="") for t in ["AI翻訳アプリが方言に対応", "病院でAI問診の実証実験", "AIで農作物の収穫時期を予測", "自治体がAIチャットを導入"]],
        "B": [make_article(t, topic="") for t in ["クラウド料金の値下げ発表", "銀行がクラウドへ全面移行"]],
    }
    out = select_articles(by_topic, topics, now, max_per_topic=2, max_total=6)
    assert [a.topic for a in out] == ["A", "B", "A", "A"]
