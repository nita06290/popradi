"""COEIROINK v2 バックエンドと設定ツールのテスト（HTTPはモック）。"""

import copy
import json

import pytest

from radio.tts import SAMPLE_RATE, CoeiroinkTTS, TTSError, TTSItem, make_wav
from tools.coeiroink_speakers import apply_settings, find_style

TSUKUYOMI = "3c37646f-3881-5374-2a83-149267990abc"
KANA = "297a5b91-f88a-6951-5841-f1e648b2e594"
SPEAKERS_API = [
    {"speakerName": "KANA", "speakerUuid": KANA, "styles": [{"styleName": "のーまる", "styleId": 30}, {"styleName": "ないしょばなし", "styleId": 33}], "version": "2.0.0"},
    {"speakerName": "つくよみちゃん", "speakerUuid": TSUKUYOMI, "styles": [{"styleName": "れいせい", "styleId": 0}], "version": "2.0.0"},
]


class Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._p, self.content = status, payload, content

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeCoeiroink:
    def __init__(self, missing_styles=()):
        self.bodies, self.missing = [], set(missing_styles)

    def get(self, url, timeout=None):
        assert url.endswith("/v1/speakers")
        return Resp(payload=SPEAKERS_API)

    def post(self, url, json=None, timeout=None):
        assert url.endswith("/v1/synthesis")
        self.bodies.append(json)
        if json["styleId"] in self.missing:
            return Resp(500)
        return Resp(content=make_wav(b"\x20\x00" * 1200, rate=SAMPLE_RATE))


@pytest.fixture
def speakers(characters):
    sp = copy.deepcopy(characters["speakers"])
    sp["sora"]["coeiroink"].update(speaker_uuid=KANA, style_id=30, styles={"excited": 33}, credit="COEIROINK:KANA")
    return sp


def test_request_body_follows_v2_api(speakers):
    fake = FakeCoeiroink()
    CoeiroinkTTS(speakers, session=fake).synthesize_many([TTSItem("haru", "テストです。", "neutral")])
    b = fake.bodies[0]
    assert b["speakerUuid"] == TSUKUYOMI and b["styleId"] == 0 and b["text"] == "テストです。"
    assert b["outputSamplingRate"] == SAMPLE_RATE and b["prosodyDetail"] == []
    for k in ("speedScale", "volumeScale", "pitchScale", "intonationScale", "prePhonemeLength", "postPhonemeLength"):
        assert k in b


def test_emotion_changes_style_and_prosody(speakers):
    fake = FakeCoeiroink()
    CoeiroinkTTS(speakers, session=fake).synthesize_many([TTSItem("sora", "はい。", "neutral"), TTSItem("sora", "ええっ！", "excited")])
    n, e = fake.bodies
    assert (n["styleId"], e["styleId"]) == (30, 33)
    assert e["intonationScale"] > n["intonationScale"] and e["speedScale"] > n["speedScale"]


def test_missing_emotion_style_falls_back_once(speakers):
    fake, logs = FakeCoeiroink(missing_styles={33}), []
    out = CoeiroinkTTS(speakers, session=fake, log=logs.append).synthesize_many(
        [TTSItem("sora", "わあ！", "excited"), TTSItem("sora", "すごい！", "excited")]
    )
    assert len(out) == 2
    assert [b["styleId"] for b in fake.bodies] == [33, 30, 30]  # 失敗したスタイルは2回目から試さない
    assert len(logs) == 1


def test_unconfigured_voice_stops_before_synthesis(characters):
    fake = FakeCoeiroink()
    with pytest.raises(TTSError, match="ソラ の COEIROINK の声が未設定"):
        CoeiroinkTTS(characters["speakers"], session=fake).synthesize_many([TTSItem("haru", "a"), TTSItem("sora", "b")])
    assert fake.bodies == []  # 途中まで合成して無駄にしない


def test_list_speakers(speakers):
    names = [s["speakerName"] for s in CoeiroinkTTS(speakers, session=FakeCoeiroink()).list_speakers()]
    assert names == ["KANA", "つくよみちゃん"]


# ---------------------------------------------------------------- 設定ツール

def test_find_style_default_and_errors():
    sp, st = find_style(SPEAKERS_API, "KANA")
    assert sp["speakerUuid"] == KANA and st["styleId"] == 30
    with pytest.raises(SystemExit, match="候補"):
        find_style(SPEAKERS_API, "存在しない")
    with pytest.raises(SystemExit, match="ないしょばなし"):
        find_style(SPEAKERS_API, "KANA:おこ")


def test_apply_settings_updates_characters(characters):
    chars = copy.deepcopy(characters)
    msgs = apply_settings(chars, SPEAKERS_API, ["sora=KANA:のーまる"], ["sora:excited=ないしょばなし"])
    c = chars["speakers"]["sora"]["coeiroink"]
    assert c["speaker_uuid"] == KANA and c["style_id"] == 30 and c["styles"] == {"excited": 33}
    assert c["credit"] == "COEIROINK:KANA" and "_comment" not in c
    assert len(msgs) == 2
    json.dumps(chars, ensure_ascii=False)  # そのまま保存できる


# ---------------------------------------------------------------- クレジット

def test_episode_credit_follows_engine(articles, characters):
    from datetime import date

    from radio.episode import build_episode
    from radio.script import template_script
    from tests.conftest import NOW

    def credits(tts):
        s = template_script(articles, characters, "x")
        ep = build_episode(episode_date=date(2026, 11, 8), script=s, lines=[], cues={}, duration=1, audio_rel=None, articles=articles,
                           characters=characters, generator={"llm": "template", "tts": tts}, warnings=[], generated_at=NOW)
        return {k: v["credit"] for k, v in ep["speakers"].items()}

    assert credits("voicevox") == {"haru": "VOICEVOX:夜語トバリ", "sora": "VOICEVOX:青山龍星"}
    assert credits("coeiroink")["haru"] == "COEIROINK:つくよみちゃん"
    assert credits("windows") == {"haru": "", "sora": ""}
