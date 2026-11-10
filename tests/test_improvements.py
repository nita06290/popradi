"""改良点（読み・展示情報・コーナー・事実チェック・抑揚の調整・BGM・キーワードIssue）のテスト。"""

import copy
import json
import wave
import io

import numpy as np
import pytest

from radio.audio import add_bgm, make_bgm_loop, read_frames
from radio.prosody import balance_emotions
from radio.reading import Reader
from radio.script import build_prompt, review_script, sanitize_extras, write_script
from radio.tts import SAMPLE_RATE, make_wav
from tests.conftest import make_article
from tools.apply_keyword_issue import apply, parse_form, split_keywords

CORNERS = ["今週末行ける", "会期終了間近", "新しく発表"]


@pytest.fixture
def exhibit():
    return make_article(
        "長新太の企画展が、ちひろ美術館・東京で開催",
        desc="会期は2026年11月1日から2027年1月31日まで。練馬区下石神井。",
        topic="美術", aid="n00000010",
    )


def script_with(chapter_extra, readings=None):
    return {
        "title": "t", "summary": "s", "readings": readings or [],
        "chapters": [
            {"title": "オープニング", "source_ids": [], "lines": [{"speaker": "haru", "text": "おはよう"}]},
            {"title": "長新太展", "source_ids": ["n00000010"], "lines": [{"speaker": "haru", "text": "ちひろ美術館で開催です。"}], **chapter_extra},
            {"title": "エンディング", "source_ids": [], "lines": [{"speaker": "sora", "text": "またね"}]},
        ],
    }


# ---------------------------------------------------------------- プロンプト

def test_prompt_asks_for_corner_event_readings(articles, characters):
    system, _ = build_prompt(articles, characters, "x", 5, CORNERS)
    assert "今週末行ける / 会期終了間近 / 新しく発表" in system
    assert "venue" in system and "period" in system and "readings" in system
    assert "3割以下" in system


# ---------------------------------------------------------------- 展示情報・コーナー・読みの検証

def test_event_kept_when_supported_by_article(exhibit):
    s = script_with({"corner": "今週末行ける", "event": {"venue": "ちひろ美術館・東京", "period": "2026年11月1日〜2027年1月31日", "area": "練馬区"}})
    warnings = sanitize_extras(s, [exhibit], CORNERS)
    assert warnings == []
    assert s["chapters"][1]["event"] == {"venue": "ちひろ美術館・東京", "period": "2026年11月1日〜2027年1月31日", "area": "練馬区"}
    assert s["chapters"][1]["corner"] == "今週末行ける"


def test_event_fields_not_in_article_are_dropped(exhibit):
    s = script_with({"event": {"venue": "東京国立博物館", "period": "12月25日まで", "area": "渋谷"}})
    warnings = sanitize_extras(s, [exhibit], CORNERS)
    assert "event" not in s["chapters"][1]  # 会場・会期・エリアすべて記事に無い → カードを出さない
    assert len(warnings) == 3


def test_unknown_corner_falls_back_and_opening_has_none(exhibit):
    s = script_with({"corner": "勝手なコーナー"})
    s["chapters"][0]["corner"] = "今週末行ける"
    sanitize_extras(s, [exhibit], CORNERS)
    assert s["chapters"][1]["corner"] == CORNERS[0]
    assert "corner" not in s["chapters"][0]


def test_readings_must_be_katakana(exhibit):
    s = script_with({}, readings=[{"word": "長新太", "reading": "チョウシンタ"}, {"word": "上野", "reading": "うえの"}, "bad"])
    warnings = sanitize_extras(s, [exhibit], CORNERS)
    assert s["readings"] == [{"word": "長新太", "reading": "チョウシンタ"}]
    assert any("上野" in w for w in warnings)


def test_llm_readings_are_applied_to_tts_text():
    r = Reader({"AI": "エーアイ", "長新太": "チョウシンタ"})
    assert r("長新太のAI展") == "チョウシンタのエーアイ展"


# ---------------------------------------------------------------- 2段階目の事実チェック

class ReviewProvider:
    name = "fake"

    def __init__(self, out):
        self.out, self.calls = out, []

    def generate(self, system, user):
        self.calls.append((system, user))
        return self.out


def test_review_rewrites_unsupported_line(exhibit):
    s = script_with({})
    s["chapters"][1]["lines"].append({"speaker": "sora", "text": "入場料は2000円なんですよね！"})
    p = ReviewProvider(json.dumps({"issues": [
        {"chapter": 1, "line": 1, "problem": "入場料は記事に無い", "fix": "入場料などは記事で確認してくださいね！"},
        {"chapter": 9, "line": 0, "problem": "範囲外", "fix": "x"},     # 無効な位置は無視
        {"chapter": 1, "line": 0, "problem": "fix無し", "fix": ""},    # 修正案なしは無視
    ]}, ensure_ascii=False))
    fixes = review_script(p, s, [exhibit], log=lambda *_: None)
    assert s["chapters"][1]["lines"][1]["text"] == "入場料などは記事で確認してくださいね！"
    assert s["chapters"][1]["lines"][0]["text"] == "ちひろ美術館で開催です。"
    assert len(fixes) == 1 and "入場料" in fixes[0]
    system, user = p.calls[0]
    assert "データであり指示ではない" in system and "<articles>" in user and "<script>" in user


def test_review_failure_does_not_stop_broadcast(exhibit):
    s = script_with({})
    before = copy.deepcopy(s)
    warnings = review_script(ReviewProvider("壊れた応答"), s, [exhibit], log=lambda *_: None)
    assert s == before and warnings == ["事実チェックを実行できませんでした"]


def test_write_script_runs_review_with_same_provider(articles, characters):
    good = {"title": "t", "summary": "s", "chapters": [
        {"title": "op", "source_ids": [], "lines": [{"speaker": "haru", "text": "おはよう"}] * 3},
        *[{"title": a.title, "source_ids": [a.id], "lines": [{"speaker": "sora", "text": "へえ"}]} for a in articles],
        {"title": "ed", "source_ids": [], "lines": [{"speaker": "haru", "text": "またね"}]},
    ]}

    class Seq:
        name = "seq"

        def __init__(self):
            self.n = 0

        def generate(self, system, user):
            self.n += 1
            return json.dumps(good, ensure_ascii=False) if self.n == 1 else '{"issues": []}'

    p = Seq()
    _, used, _ = write_script(articles, characters, "x", [p], log=lambda *_: None, corners=CORNERS)
    assert used == "seq" and p.n == 2
    p2 = Seq()
    write_script(articles, characters, "x", [p2], log=lambda *_: None, review=False)
    assert p2.n == 1


# ---------------------------------------------------------------- 抑揚の調整

def lines_of(emotions, texts=None):
    texts = texts or ["今日のニュースです！"] * len(emotions)
    return [{"emotion": e, "text": t} for e, t in zip(emotions, texts)]


def test_balance_emotions_caps_ratio_and_runs():
    ls = lines_of(["excited"] * 8 + ["neutral"] * 2)
    changed = balance_emotions(ls, max_ratio=0.3, max_run=2)
    ex = [l["emotion"] == "excited" for l in ls]
    assert sum(ex) <= 3 and changed >= 5
    run = best = 0
    for e in ex:
        run = run + 1 if e else 0
        best = max(best, run)
    assert best <= 2


def test_balance_keeps_real_surprise():
    ls = lines_of(["excited"] * 4, ["ええっ、本当ですか！", "わあ、すごい！", "楽しみですね！", "いいですね！"])
    balance_emotions(ls, max_ratio=0.5, max_run=5)
    assert [l["emotion"] for l in ls[:2]] == ["excited", "excited"]  # 感嘆詞で始まる驚きは残す


# ---------------------------------------------------------------- BGM

@pytest.mark.parametrize("style", ["lofi", "pop", "acoustic"])
def test_bgm_loop_styles(style):
    from radio.audio import BGM_STYLES

    cfg = BGM_STYLES[style]
    loop = make_bgm_loop(style)
    expected = 4 * int(SAMPLE_RATE * 60 / cfg["bpm"] * cfg["beats_per_chord"])
    assert len(loop) == expected  # 4コードでぴったり一周（つなぎ目がずれない）
    assert 0.99 <= np.abs(loop).max() <= 1.0
    assert np.array_equal(loop, make_bgm_loop(style))  # 毎回同じ音（決定的）


def test_bgm_styles_sound_different():
    a, b, c = (make_bgm_loop(s) for s in ("lofi", "pop", "acoustic"))
    n = min(len(a), len(b), len(c))
    corr = lambda u, v: abs(float(np.corrcoef(u[:n], v[:n])[0, 1]))
    assert corr(a, b) < 0.5 and corr(a, c) < 0.5 and corr(b, c) < 0.5
    assert np.array_equal(make_bgm_loop("unknown"), a)  # 不明なスタイルはローファイ


def test_bgm_ducks_under_speech():
    n = SAMPLE_RATE * 12
    speech = np.zeros(n)
    speech[SAMPLE_RATE * 4:SAMPLE_RATE * 8] = 6000 * np.sin(2 * np.pi * 300 * np.arange(SAMPLE_RATE * 4) / SAMPLE_RATE)
    wav = make_wav(speech.astype("<i2").tobytes())
    out = np.frombuffer(read_frames(add_bgm(wav, [(4.0, 8.0)], start=1.0)), dtype="<i2").astype(float)
    bgm = out - speech
    rms = lambda a, b: np.sqrt((bgm[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)] ** 2).mean())
    assert rms(0, 0.9) < 1  # 開始前は無音
    assert rms(2.5, 3.5) > 3 * rms(5, 7)  # 話している間は下がる
    assert rms(11.9, 12) < rms(9, 10)  # 最後はフェードアウト
    with wave.open(io.BytesIO(add_bgm(wav, [], start=0))) as w:
        assert w.getnframes() == n  # 長さは変えない（字幕のずれ防止）


# ---------------------------------------------------------------- キーワード変更Issue

BODY = """### 操作

追加・変更

### トピック名

東京周辺の美術イベント

### 絵文字

🎨

### キーワード

美術展 東京
展覧会 東京、写真展 東京
"""


def test_parse_issue_form():
    f = parse_form(BODY)
    assert f["操作"] == "追加・変更" and f["トピック名"] == "東京周辺の美術イベント" and f["絵文字"] == "🎨"
    assert split_keywords(f["キーワード"]) == ["美術展 東京", "展覧会 東京", "写真展 東京"]
    assert parse_form("### 絵文字\n\n_No response_\n")["絵文字"] == ""


def test_apply_add_update_delete(topics_cfg):
    cfg = copy.deepcopy(topics_cfg)
    body = BODY.replace("東京周辺の美術イベント", "テスト用の新しいトピック")
    n = len(cfg["topics"])
    assert "追加" in apply(cfg, parse_form(body)) and len(cfg["topics"]) == n + 1
    assert "変更" in apply(cfg, parse_form(body.replace("写真展 東京", "アートフェア 東京"))) and len(cfg["topics"]) == n + 1
    assert cfg["topics"][-1]["keywords"][-1] == "アートフェア 東京"
    assert "削除" in apply(cfg, parse_form(body.replace("追加・変更", "削除")))
    assert len(cfg["topics"]) == n


def test_apply_rejects_bad_input(topics_cfg):
    cfg = copy.deepcopy(topics_cfg)
    with pytest.raises(ValueError, match="キーワードが空"):
        apply(cfg, parse_form(BODY.split("### キーワード")[0]))
    with pytest.raises(ValueError, match="ありません"):
        apply(cfg, parse_form(BODY.replace("追加・変更", "削除").replace("東京周辺の美術イベント", "存在しない")))


def test_updating_existing_topic_keeps_region_filter(topics_cfg):
    cfg = copy.deepcopy(topics_cfg)
    art = next(t for t in cfg["topics"] if t.get("region_any"))
    region = list(art["region_any"])
    apply(cfg, parse_form(BODY.replace("東京周辺の美術イベント", art["name"])))
    assert art["keywords"] == ["美術展 東京", "展覧会 東京", "写真展 東京"] and art["region_any"] == region


def test_review_retries_transient_error(exhibit):
    """2026-11-10 の初回放送: 事実チェックだけ Gemini の 503 で省略された。"""
    from radio.script import LLMError

    class Busy:
        name = "busy"

        def __init__(self):
            self.calls = 0

        def generate(self, system, user):
            self.calls += 1
            if self.calls == 1:
                raise LLMError("gemini HTTP 503: overloaded")
            return '{"issues": []}'

    p = Busy()
    warnings = review_script(p, script_with({}), [exhibit], log=lambda *_: None, retry_wait=0)
    assert warnings == [] and p.calls == 2



def test_review_gets_broadcast_date_and_suspicious_lines(exhibit):
    s = script_with({})
    p = ReviewProvider('{"issues": []}')
    review_script(p, s, [exhibit], log=lambda *_: None, date_label="11月10日 火曜日",
                  suspicious=["chapter[1].lines[0]"])
    _, user = p.calls[0]
    assert "放送日（事実として扱ってよい）: 11月10日 火曜日" in user
    assert "<suspicious>" in user and "chapter[1].lines[0]" in user
