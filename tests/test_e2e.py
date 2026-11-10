"""パイプライン全体のE2E（サンプルRSS → テンプレート台本 → 無音TTS → site/）。"""

import json
from pathlib import Path

from radio.pipeline import main
from tools.check_site import check


FIXTURE = str(Path(__file__).parent / "fixtures" / "topics_sample.json")


def run(site, *extra, now="2026-11-08T05:00:00+09:00"):
    return main(["--site", str(site), "--sample", "--topics", FIXTURE, "--llm", "template", "--tts", "silent", "--now", now, *extra])


def test_full_pipeline_builds_valid_site(tmp_path):
    site = tmp_path / "site"
    assert run(site) == 0
    assert check(site) == []
    ep = json.loads((site / "episodes/2026-11-08.json").read_text(encoding="utf-8"))
    titles = [s["title"] for s in ep["sources"]]
    # 期間外の古い記事は落ちる
    assert not any("古い記事" in t for t in titles)
    # 同じニュースの別媒体版は1本だけ
    assert sum("ミライ技研" in t for t in titles) == 1
    # 全トピックから最低1本
    assert {s["topic"] for s in ep["sources"]} == {"生成AI", "クラウド", "開発技術", "データサイエンス"}
    assert len(ep["sources"]) <= 6
    # 番組の流れ: オープニング → ニュース → エンディング
    assert ep["chapters"][0]["title"] == "オープニング" and ep["chapters"][-1]["title"] == "エンディング"
    assert ep["generator"] == {"llm": "template", "tts": "silent", "sample": True}
    station = json.loads((site / "station.json").read_text(encoding="utf-8"))
    assert station["station_name"] == "ポプラジ" and len(station["topics"]) == 4
    assert (site / "index.html").exists() and (site / "sw.js").exists()


def test_next_day_does_not_repeat_news(tmp_path):
    site = tmp_path / "site"
    assert run(site) == 0
    assert run(site, now="2026-11-09T05:00:00+09:00") == 0
    d1 = json.loads((site / "episodes/2026-11-08.json").read_text(encoding="utf-8"))
    d2 = json.loads((site / "episodes/2026-11-09.json").read_text(encoding="utf-8"))
    # 前日に流した記事は、同じURLでも別媒体の同じ見出しでも再登場しない → 未紹介の1本だけ残る
    assert not ({s["url"] for s in d1["sources"]} & {s["url"] for s in d2["sources"]})
    assert [s["title"] for s in d2["sources"]] == ["【サンプル】軽量LLMをスマホで動かす実験、オープンソースで公開"]
    index = json.loads((site / "episodes/index.json").read_text(encoding="utf-8"))
    assert [e["date"] for e in index["episodes"]] == ["2026-11-09", "2026-11-08"]
    assert check(site) == []


def test_skips_broadcast_when_no_new_news(tmp_path):
    site = tmp_path / "site"
    for day in ("08", "09", "10"):
        assert run(site, now=f"2026-11-{day}T05:00:00+09:00") == 0
    assert not (site / "episodes/2026-11-10.json").exists()  # ネタ切れの日は休止
    assert check(site) == []
