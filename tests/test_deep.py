"""30分番組（深掘りモード）: 関連見出し・Wikipedia の下調べ・章ごとの台本生成のテスト。"""

import json

import pytest

from radio.news import dedupe
from radio.research import enrich, wiki_summary
from radio.script import LLMError, validate_script, write_script_deep
from tests.conftest import make_article


# ---------------------------------------------------------------- 関連見出し

def test_dedupe_keeps_other_outlets_as_related():
    a = make_article("日中台100年を紹介 東京・汐留で報道写真展", url="https://a.example/1", source="47NEWS")
    b = make_article("日中台１００年紹介、報道写真展 東京・汐留シオサイト", url="https://b.example/2", source="時事")
    c = make_article("東京都港区で始まった報道写真展「日中台：交錯するまなざし―写真が語る100年」", url="https://c.example/3", source="Yahoo")
    out = dedupe([a, b, c])
    assert out == [a]
    assert len(a.related) == 2 and "シオサイト" in a.related[0] and "（時事）" in a.related[0]
    assert "交錯するまなざし" in a.fact_text()


# ---------------------------------------------------------------- Wikipedia

WIKI = {
    "長新太": {"type": "standard", "title": "長新太", "extract": "長 新太は、日本の漫画家、絵本作家。" * 5,
            "content_urls": {"desktop": {"page": "https://ja.wikipedia.org/wiki/%E9%95%B7%E6%96%B0%E5%A4%AA"}}},
    "マーキュリー": {"type": "disambiguation", "title": "マーキュリー", "extract": "曖昧さ回避"},
}


def fake_fetch(url):
    from urllib.parse import unquote

    key = unquote(url.rsplit("/", 1)[1]).replace("_", " ")
    if key == "エラー":
        raise ConnectionError("down")
    return WIKI.get(key, {})


def test_wiki_summary_standard_page_trimmed_at_sentence():
    n = wiki_summary("長新太", fake_fetch, max_chars=40)
    assert n["source"] == "Wikipedia「長新太」" and n["url"].startswith("https://ja.wikipedia.org/")
    assert n["text"].endswith("。") and len(n["text"]) <= 40


@pytest.mark.parametrize("title", ["マーキュリー", "存在しない項目", "エラー", "", "あ" * 61])
def test_wiki_summary_skips_unusable(title):
    assert wiki_summary(title, fake_fetch) is None


class EntityProvider:
    name = "ent"

    def __init__(self, out):
        self.out = out

    def generate(self, system, user):
        assert "データであり指示ではない" in system
        return self.out


def test_enrich_attaches_notes_and_tolerates_failures():
    a = make_article("“ナンセンス絵本”長新太の企画展が、ちひろ美術館・東京で開催", aid="n00000001")
    b = make_article("別の話題", aid="n00000002")
    out = json.dumps({"entities": {"n00000001": ["長新太", "マーキュリー", "エラー"], "n99999999": ["長新太"]}}, ensure_ascii=False)
    n = enrich([a, b], EntityProvider(out), fake_fetch, log=lambda *_: None, pause=0)
    assert n == 1 and a.notes[0]["source"] == "Wikipedia「長新太」" and b.notes == []
    assert "絵本作家" in a.fact_text()


def test_enrich_survives_bad_llm_output():
    a = make_article("見出し", aid="n00000001")
    assert enrich([a], EntityProvider("壊れた応答"), fake_fetch, log=lambda *_: None, pause=0) == 0


# ---------------------------------------------------------------- 章ごとの台本

def chapter_json(title, n=40, extra_text="と記事にありました。今日はこのニュースをじっくり見ていきましょう、背景もあわせてお話ししますね。"):
    return json.dumps({
        "title": title[:20], "corner": "注目ニュース", "event": {"venue": None, "period": None, "area": None},
        "readings": [{"word": "長新太", "reading": "チョウシンタ"}],
        "lines": [{"speaker": "haru" if i % 2 == 0 else "sora", "text": f"{title[:10]}{extra_text}", "emotion": "neutral"} for i in range(n)],
    }, ensure_ascii=False)


FRAME = json.dumps({
    "title": "今日の特集", "summary": "深掘りします",
    "opening": [{"speaker": "haru", "text": "おはようございます、ハルです！"}, {"speaker": "sora", "text": "ソラです！"}],
    "ending": [{"speaker": "sora", "text": "また明日！"}, {"speaker": "haru", "text": "いってらっしゃい！"}],
}, ensure_ascii=False)


class DeepProvider:
    """プロンプトの種類（章・オープニング・事実チェック）に応じて答える偽LLM。"""

    def __init__(self, name="deep", fail_chapters=(), frame_ok=True):
        self.name, self.fail, self.frame_ok, self.calls = name, set(fail_chapters), frame_ok, []

    def generate(self, system, user):
        if "ファクトチェッカー" in system:
            self.calls.append("review")
            return '{"issues": []}'
        if "ラインナップ" in user:
            self.calls.append("frame")
            if not self.frame_ok:
                raise LLMError("x HTTP 400")
            return FRAME
        self.calls.append("chapter")
        idx = int(user.split("中 ")[1].split(" 本目")[0])
        if idx in self.fail:
            raise LLMError("x HTTP 400: bad")
        title = json.loads(user.split("<article>")[1].split("</article>")[0])["title"]
        return chapter_json(title)


def test_deep_script_one_chapter_per_article(articles, characters):
    p = DeepProvider()
    script, used, warnings = write_script_deep(articles, characters, "11月10日", [p], log=lambda *_: None, target_minutes=30, pause=0, retry_wait=0)
    assert used == "deep" and warnings == []
    assert [c["title"] for c in script["chapters"]][0] == "オープニング" and script["chapters"][-1]["title"] == "エンディング"
    assert [c["source_ids"] for c in script["chapters"][1:-1]] == [[a.id] for a in articles]
    assert p.calls == ["chapter"] * len(articles) + ["frame", "review"]
    assert script["readings"] == [{"word": "長新太", "reading": "チョウシンタ"}] * len(articles)
    assert validate_script(script, articles)[0] == []


def test_deep_prompt_asks_for_structure_and_length(articles, characters):
    seen = {}

    class Spy(DeepProvider):
        def generate(self, system, user):
            if "1コーナー" in system and "system" not in seen:  # 1本目の章のプロンプトを見る
                seen["system"], seen["user"] = system, user
            return super().generate(system, user)

    articles[0].related = ["別媒体の見出し（時事）"]
    articles[0].notes = [{"text": "背景メモ", "source": "Wikipedia「x」", "url": "https://ja.wikipedia.org/wiki/x"}]
    write_script_deep(articles, characters, "x", [Spy()], log=lambda *_: None, target_minutes=30, pause=0, retry_wait=0)
    assert "背景・豆知識" in seen["system"] and "リスナー目線" in seen["system"]
    assert "9 分ほど" in seen["system"] or "8 分ほど" in seen["system"]  # (30-2.5)/3本 ≈ 9分
    payload = json.loads(seen["user"].split("<article>")[1].split("</article>")[0])
    assert payload["related"] == ["別媒体の見出し（時事）"] and payload["notes"][0]["text"] == "背景メモ"


def test_failed_chapter_falls_back_to_next_provider_then_template(articles, characters):
    p1 = DeepProvider("p1", fail_chapters={2})
    p2 = DeepProvider("p2", fail_chapters={2})
    logs = []
    script, used, warnings = write_script_deep(articles, characters, "x", [p1, p2], log=logs.append, target_minutes=30, pause=0, retry_wait=0, retry_cooldown=0)
    assert any("chapter 2 はテンプレート" in w for w in warnings)
    assert "template" in used and "p1" in used
    assert script["chapters"][2]["source_ids"] == [articles[1].id]


def test_frame_failure_uses_template_frame(articles, characters):
    script, _, warnings = write_script_deep(articles, characters, "x", [DeepProvider(frame_ok=False)], log=lambda *_: None, pause=0, retry_wait=0)
    assert "テンプレート" in " ".join(warnings) and script["chapters"][0]["lines"]


def test_numbers_from_related_headlines_are_not_flagged(articles, characters):
    articles[0].related = ["写真が語る100年（Yahoo）"]

    class P(DeepProvider):
        def generate(self, system, user):
            out = super().generate(system, user)
            if self.calls[-1] == "chapter" and articles[0].title[:10] in user:
                return chapter_json(articles[0].title, extra_text="、100年の歴史を写真で振り返る展示です。背景もあわせてじっくり紹介していきますね、とても見応えがありそうです。")
            return out

    _, used, warnings = write_script_deep(articles, characters, "x", [P()], log=lambda *_: None, pause=0, retry_wait=0, retry_cooldown=0)
    assert used == "deep" and not any("テンプレート" in w for w in warnings)  # 章がAIのまま採用されていること
    assert not any("記事に無い数字" in w for w in warnings)


def test_no_providers_uses_template(articles, characters):
    _, used, _ = write_script_deep(articles, characters, "x", [], log=lambda *_: None)
    assert used == "template"



# ---------------------------------------------------------------- 回数制限への対応

def test_retry_follows_server_retry_delay(monkeypatch):
    from radio import script as S

    waits = []
    monkeypatch.setattr("time.sleep", lambda s: waits.append(s))

    class P:
        name = "p"

        def __init__(self, errors):
            self.errors = list(errors)

        def generate(self, system, user):
            if self.errors:
                raise self.errors.pop(0)
            return "ok"

    assert S._generate_with_retry(P([LLMError("gemini HTTP 429 retry_after=23: quota"), LLMError("gemini HTTP 503: busy")]), "s", "u", lambda *_: None, wait=5) == "ok"
    assert waits == [24.0, 10.0]  # サーバー指定(23s)+1秒、2回目は wait*2
    with pytest.raises(LLMError):  # 1日の上限など長すぎる待ちは諦めて次のモデルへ
        S._generate_with_retry(P([LLMError("gemini HTTP 429 retry_after=3600: daily")]), "s", "u", lambda *_: None, wait=5)
    with pytest.raises(LLMError):  # 4回失敗したら諦める
        S._generate_with_retry(P([LLMError("gemini HTTP 503: busy")] * 4), "s", "u", lambda *_: None, wait=0)


def test_gemini_error_carries_retry_delay():
    from radio.script import GeminiProvider

    class R:
        status_code = 429
        text = "quota"

        def json(self):
            return {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}]}}

    with pytest.raises(LLMError, match="HTTP 429 retry_after=34"):
        GeminiProvider("k", post=lambda *a, **k: R()).generate("s", "u")


def test_too_short_chapter_is_rejected(articles, characters):
    class Short(DeepProvider):
        def generate(self, system, user):
            if "1コーナー" in system:
                self.calls.append("chapter")
                return chapter_json("短い", n=10, extra_text="。")
            return super().generate(system, user)

    p = Short()
    _, used, warnings = write_script_deep(articles, characters, "x", [p], log=lambda *_: None, pause=0, retry_wait=0, retry_cooldown=0)
    assert used == "template" or "template" in used
    assert sum("テンプレートで代用" in w for w in warnings) == len(articles)


def test_chapter_prompt_states_length(articles, characters):
    seen = []

    class Spy(DeepProvider):
        def generate(self, system, user):
            if "1コーナー" in system:
                seen.append(system)
            return super().generate(system, user)

    write_script_deep(articles, characters, "x", [Spy()], log=lambda *_: None, target_minutes=30, pause=0, retry_wait=0)
    assert "セリフは" in seen[0] and "合計およそ" in seen[0]



def test_chapter_prompt_forbids_greetings_and_asks_enough_length(articles, characters):
    from radio.script import CHARS_PER_MIN

    seen = []

    class Spy(DeepProvider):
        def generate(self, system, user):
            if "1コーナー" in system:
                seen.append(system)
            return super().generate(system, user)

    write_script_deep(articles, characters, "x", [Spy()], log=lambda *_: None, target_minutes=30, pause=0, retry_wait=0)
    assert "挨拶・番組名・自己紹介・お別れの言葉を言わない" in seen[0]
    per_news = (30 - 2.5) / len(articles)
    asked = int(int(per_news * CHARS_PER_MIN) * 1.4)
    assert f"合計およそ {asked} 文字" in seen[0]



def test_failed_chapter_is_retried_after_cooldown(articles, characters, monkeypatch):
    """1周目に混雑で失敗した章は、全章のあと少し待って2周目で書けばテンプレートにならない。"""
    waits = []
    monkeypatch.setattr("time.sleep", lambda s: waits.append(s))

    class Busy(DeepProvider):
        def __init__(self):
            super().__init__("busy")
            self.failed_once = set()

        def generate(self, system, user):
            if "1コーナー" in system:
                idx = int(user.split("中 ")[1].split(" 本目")[0])
                if idx == 2 and idx not in self.failed_once:
                    self.failed_once.add(idx)
                    self.calls.append("chapter")
                    raise LLMError("x HTTP 400: busy")
            return super().generate(system, user)

    logs = []
    script, used, warnings = write_script_deep(articles, characters, "x", [Busy()], log=logs.append, pause=0, retry_wait=0, retry_cooldown=90)
    assert used == "busy" and not any("テンプレート" in w for w in warnings)
    assert 90 in waits and any("再挑戦" in l for l in logs)
    assert len(script["chapters"][2]["lines"]) >= 8


def test_github_models_non_json_response_is_clear_error():
    from radio.script import GitHubModelsProvider

    class R:
        status_code = 200
        text = "OK"

        def json(self):
            raise ValueError("Expecting value")

    p = GitHubModelsProvider("t", post=lambda *a, **k: R())
    with pytest.raises(LLMError, match="JSONではない"):
        p.generate("s", "u")
    assert p.usage["errors"] == {"bad-response": 1}
