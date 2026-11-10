"""声の違い・抑揚・ノイズ処理のテスト。"""

import io
import re
import sys
import wave

import numpy as np
import pytest

from radio.audio import clean_voice, read_frames
from radio.prosody import EMOTIONS, build_ssml, infer_emotion, resolve_emotion, split_sentences, voicevox_params, voicevox_style
from radio.tts import SAMPLE_RATE, TTSError, TTSItem, VoicevoxTTS, WindowsTTS, make_wav, to_standard_wav


def samples(wav_bytes):
    return np.frombuffer(read_frames(wav_bytes), dtype="<i2").astype(float)


def median_f0(wav_bytes):
    """自己相関による声の高さ(Hz)の中央値。"""
    x, sr, n = samples(wav_bytes), SAMPLE_RATE, int(SAMPLE_RATE * 0.04)
    fs = []
    for i in range(0, len(x) - n, n // 2):
        fr = x[i : i + n] * np.hanning(n)
        if np.sqrt((fr**2).mean()) < 500:
            continue
        ac = np.correlate(fr, fr, "full")[n - 1 :]
        lo, hi = sr // 400, sr // 70
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > 0.4 * ac[0]:
            fs.append(sr / k)
    return float(np.median(fs))


# ---------------------------------------------------------------- 感情推定

@pytest.mark.parametrize(
    "text,emo",
    [
        ("これって生活にどう関わりますか？", "question"),
        ("どれくらい話題になってるんですか", "question"),
        ("ええっ、もうそんな段階なんですか！", "question"),
        ("ええっ、もうそんな段階！", "excited"),
        ("へえ〜、そうなんですね！", "excited"),
        ("なるほど、そういうことなんですね。", "thinking"),
        ("ソラでした。", "calm"),
        ("今日は6本のニュースを紹介します。", "neutral"),
    ],
)
def test_infer_emotion(text, emo):
    assert infer_emotion(text) == emo


def test_resolve_emotion_prefers_valid_llm_value():
    assert resolve_emotion("普通の文です。", "excited") == "excited"
    assert resolve_emotion("普通の文です。", "angry") == "neutral"  # 未知の値は推定に戻す


def test_resolve_emotion_neutral_from_llm_uses_text_cues():
    """flash-lite が全部 neutral を付けても、文面の「？」「！」「なるほど」で抑揚をつける。"""
    assert resolve_emotion("それってどういうことですか？", "neutral") == "question"
    assert resolve_emotion("ええっ、すごい！", "neutral") == "excited"
    assert resolve_emotion("なるほど、そういうことなんですね。", "neutral") == "thinking"
    assert resolve_emotion("今日は晴れです。", "neutral") == "neutral"
    assert resolve_emotion("今日は晴れです。", None) == "neutral"
    assert set(EMOTIONS) == {"neutral", "excited", "question", "thinking", "calm"}


# ---------------------------------------------------------------- SSML

def test_split_sentences():
    assert split_sentences("はい。そうです！本当？") == ["はい。", "そうです！", "本当？"]


def test_ssml_varies_pitch_and_adds_pauses():
    ssml = build_ssml("最初の文です。次の文、ここです。最後です。", "Microsoft Haruka", "neutral", 6, 1.05, seed=1)
    pitches = re.findall(r'pitch="([+-]\d+)%"', ssml)
    assert len(pitches) == 3 and len(set(pitches)) == 3  # 文ごとに高さが違う＝単調にならない
    assert ssml.count('<break time="230ms"/>') == 2  # 文の間
    assert '<break time="110ms"/>' in ssml  # 読点の間
    assert 'rate="1.05"' in ssml


def test_ssml_question_rises_at_end_and_excited_is_higher():
    q = build_ssml("それってどうなるんですか？", "V", "question", seed=0)
    assert q.count("<prosody") == 2 and 'pitch="+14%">ですか？</prosody>' in q
    base = int(re.search(r'pitch="([+-]\d+)%"', build_ssml("今日は晴れ。", "V", "neutral", seed=3)).group(1))
    exc = int(re.search(r'pitch="([+-]\d+)%"', build_ssml("今日は晴れ。", "V", "excited", seed=3)).group(1))
    assert exc - base == 9


def test_ssml_escapes_xml():
    ssml = build_ssml("A&B <テスト>", "V", "neutral")
    assert "A&amp;B &lt;テスト&gt;" in ssml


# ---------------------------------------------------------------- VOICEVOX

def test_voicevox_params_by_emotion():
    base = {"speaker": 12, "speed": 1.1, "pitch": 0.02, "intonation": 1.3}
    n, e, t = (voicevox_params(base, k) for k in ("neutral", "excited", "thinking"))
    assert n["intonationScale"] == 1.3 and e["intonationScale"] > n["intonationScale"] > t["intonationScale"]
    assert e["speedScale"] > n["speedScale"] > t["speedScale"]
    assert voicevox_style({"speaker": 12, "styles": {"excited": 32}}, "excited") == 32
    assert voicevox_style({"speaker": 12, "styles": {"excited": 32}}, "question") == 12


class FakeResp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._p, self.content = status, payload, content

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeVoicevox:
    """存在しないスタイル(32)では 422 を返すエンジンのモック。"""

    def __init__(self):
        self.calls = []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[1], params["speaker"], json))
        if params["speaker"] == 32:
            return FakeResp(422)
        if url.endswith("/audio_query"):
            return FakeResp(payload={"speedScale": 1.0, "accent_phrases": []})
        return FakeResp(content=make_wav(b"\x10\x00" * 2400, rate=SAMPLE_RATE))


def test_voicevox_applies_emotion_and_falls_back_on_missing_style(characters):
    import copy

    sp = copy.deepcopy(characters["speakers"])
    sp["sora"]["voicevox"].update(speaker=12, styles={"excited": 32})
    sp["haru"]["voicevox"]["speaker"] = 8
    sess = FakeVoicevox()
    logs = []
    tts = VoicevoxTTS(sp, session=sess, log=logs.append)
    out = tts.synthesize_many([TTSItem("sora", "ええっ！", "excited"), TTSItem("sora", "すごい！", "excited"), TTSItem("haru", "はい。", "calm")])
    assert len(out) == 3
    synth = [c for c in sess.calls if c[0] == "synthesis"]
    assert [c[1] for c in synth] == [12, 12, 8]  # 32は失敗→基本スタイル、2回目以降は32を試さない
    assert sum(1 for c in sess.calls if c[1] == 32) == 1 and len(logs) == 1
    q = synth[0][2]
    assert q["intonationScale"] > sp["sora"]["voicevox"]["intonation"]
    assert q["outputSamplingRate"] == SAMPLE_RATE and q["outputStereo"] is False


# ---------------------------------------------------------------- 音声処理

def test_to_standard_wav_resamples_16k_to_24k():
    w16 = make_wav(b"\x00\x01" * 16000, rate=16000)
    out = to_standard_wav(w16)
    with wave.open(io.BytesIO(out)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 24000


def make_noisy_voice(amp=8000, noise=40, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(SAMPLE_RATE * 1.0)) / SAMPLE_RATE
    voice = np.where((t > 0.3) & (t < 0.7), amp * np.sin(2 * np.pi * 220 * t), 0)
    x = voice + rng.normal(0, noise, len(t)) + 300  # 声 + サーッ音 + 直流
    return make_wav(np.clip(x, -32768, 32767).astype("<i2").tobytes())


def test_clean_voice_trims_gates_and_fades():
    out = samples(clean_voice(make_noisy_voice()))
    assert 0.4 <= len(out) / SAMPLE_RATE <= 0.55  # 前後0.3秒の無音ノイズを削除
    assert abs(out[0]) < 50 and abs(out[-1]) < 50  # フェードでプチッとしない
    assert abs(out.mean()) < 100  # 直流除去


def test_clean_voice_equalizes_loudness():
    quiet, loud = samples(clean_voice(make_noisy_voice(amp=1500))), samples(clean_voice(make_noisy_voice(amp=20000)))
    rq, rl = np.sqrt((quiet**2).mean()), np.sqrt((loud**2).mean())
    assert abs(20 * np.log10(rq / rl)) < 1.0  # 2人の音量差を1dB以内に
    assert np.abs(loud).max() <= 32767 * 10 ** (-1.5 / 20) + 1


def test_clean_voice_keeps_silence_as_is():
    s = make_wav(b"\x00\x00" * 2400)
    assert clean_voice(s) == s


# ---------------------------------------------------------------- 実音声（Windowsのみ）

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows OneCore 音声のみ")


@win
def test_windows_tts_two_distinct_voices(characters):
    text = "おはようございます、今日のニュースをお届けします。"
    haru, sora = WindowsTTS(characters["speakers"]).synthesize_many([TTSItem("haru", text), TTSItem("sora", text)])
    assert haru != sora
    f_haru, f_sora = median_f0(haru), median_f0(sora)
    assert f_haru > f_sora * 1.4, (f_haru, f_sora)  # ハル(女性)とソラ(男性)で明確に高さが違う


@win
def test_windows_tts_emotion_changes_pitch(characters):
    text = "今日はとても良い天気になりそうです。"
    neutral, excited = WindowsTTS(characters["speakers"]).synthesize_many(
        [TTSItem("sora", text, "neutral", seed=5), TTSItem("sora", text, "excited", seed=5)]
    )
    assert median_f0(excited) > median_f0(neutral) * 1.03


@win
def test_windows_tts_missing_voice_fails_loudly(characters):
    sp = {k: dict(v) for k, v in characters["speakers"].items()}
    sp["sora"] = {**sp["sora"], "windows": {"voice": "Microsoft NoSuchVoice"}}
    with pytest.raises(TTSError, match="VOICE_NOT_FOUND"):
        WindowsTTS(sp).synthesize_many([TTSItem("sora", "テスト")])
