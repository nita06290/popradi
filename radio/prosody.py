"""抑揚づけ: セリフの感情を推定し、TTSごとの韻律パラメータに変換する。

LLMが emotion を付けてくればそれを使い、無ければ文面から推定する。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from xml.sax.saxutils import escape

EMOTIONS = ("neutral", "excited", "question", "thinking", "calm")

EXCITED_HEAD = re.compile(r"^(ええっ|えっ|おお|おーっ|わあ|わぁ|わー|へえ|へぇ|すごい|やった|まさか|なんと)")
THINKING = re.compile(r"(なるほど|うーん|ふむふむ|ふむ|そうですねえ|…|、、)")


def infer_emotion(text: str) -> str:
    t = text.strip()
    if re.search(r"[?？]\s*$", t) or re.search(r"(ですか|ますか|でしょうか|かな)[。！!]?\s*$", t):
        return "question"
    if EXCITED_HEAD.search(t) or (("！" in t or "!" in t) and len(t) < 40):
        return "excited"
    if THINKING.search(t):
        return "thinking"
    if re.search(r"(でした|ください|しょう)。?\s*$", t):
        return "calm"
    return "neutral"


def resolve_emotion(text: str, given: str | None) -> str:
    """LLM の指定を優先。ただし "neutral"（や未指定）のときは文面から推定する。

    flash-lite などは大半のセリフに neutral を付けて抑揚が平坦になるため（2026-11-10 は158中127が neutral）。
    """
    if given in EMOTIONS and given != "neutral":
        return given
    return infer_emotion(text)


@dataclass(frozen=True)
class Prosody:
    pitch_pct: float  # 声の高さ（%）
    rate: float  # 話速（倍）
    intonation: float  # VOICEVOX intonationScale の倍率（抑揚の強さ）
    vv_pitch: float  # VOICEVOX pitchScale への加算
    end_rise_pct: float = 0.0  # 文末を上げる量（質問の語尾）


PROSODY = {
    "neutral": Prosody(0, 1.00, 1.00, 0.00),
    "excited": Prosody(9, 1.07, 1.35, 0.03),
    "question": Prosody(3, 1.00, 1.20, 0.01, end_rise_pct=14),
    "thinking": Prosody(-4, 0.92, 0.90, -0.02),
    "calm": Prosody(-2, 0.97, 1.05, -0.01),
}

SENTENCE_RE = re.compile(r"[^。！？!?]+[。！？!?]*")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_RE.findall(text) if p.strip()]
    return parts or [text]


def _pct(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}{abs(v):.0f}%"


def build_ssml(text: str, voice: str, emotion: str, base_pitch_pct: float = 0, base_rate: float = 1.0, seed: int = 0) -> str:
    """Windows OneCore 向け SSML。文ごとに高さを揺らし、句読点に間を入れて単調さを消す。"""
    p = PROSODY.get(emotion, PROSODY["neutral"])
    rng = random.Random(seed)
    rate = base_rate * p.rate
    chunks = []
    sentences = split_sentences(text)
    for i, s in enumerate(sentences):
        # 1文目は少し高く入り、後ろの文は少し落ち着かせる（話し言葉の自然な下降）
        drift = (3 if i == 0 else -1.5 * min(i, 2)) + rng.uniform(-2, 2)
        pitch = base_pitch_pct + p.pitch_pct + drift
        body = escape(s).replace("、", '、<break time="110ms"/>')
        last = i == len(sentences) - 1
        if last and p.end_rise_pct and len(s) > 4:
            head, tail = escape(s[:-4]).replace("、", '、<break time="110ms"/>'), escape(s[-4:])
            body = f'{head}<prosody pitch="{_pct(p.end_rise_pct)}">{tail}</prosody>'
        chunks.append(f'<prosody pitch="{_pct(pitch)}" rate="{rate:.2f}">{body}</prosody>')
        if not last:
            chunks.append('<break time="230ms"/>')
    if emotion == "thinking":
        chunks.insert(0, '<break time="150ms"/>')
    inner = "".join(chunks)
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="ja-JP">'
        f'<voice name="{escape(voice)}">{inner}</voice></speak>'
    )


def voicevox_params(base: dict, emotion: str) -> dict:
    """characters.json の voicevox 設定 + 感情 → audio_query に上書きする値。"""
    p = PROSODY.get(emotion, PROSODY["neutral"])
    return {
        "speedScale": round(base.get("speed", 1.1) * p.rate, 3),
        "pitchScale": round(base.get("pitch", 0.0) + p.vv_pitch, 3),
        "intonationScale": round(base.get("intonation", 1.2) * p.intonation, 3),
        "volumeScale": base.get("volume", 1.0),
    }


def voicevox_style(base: dict, emotion: str) -> int:
    return int(base.get("styles", {}).get(emotion, base["speaker"]))


def balance_emotions(lines: list[dict], max_ratio: float = 0.3, max_run: int = 2) -> int:
    """excited が多すぎると騒がしいので間引く（lines の emotion をその場で更新）。変更数を返す。

    - 感嘆詞で始まる本当の驚き（ええっ、わあ…）は残し、それ以外の excited から neutral に戻す
    - excited が max_run を超えて連続しないようにする
    """
    changed = 0

    def soft(i: int) -> bool:
        return lines[i]["emotion"] == "excited" and not EXCITED_HEAD.search(lines[i]["text"].strip())

    run = 0
    for i, ln in enumerate(lines):
        run = run + 1 if ln["emotion"] == "excited" else 0
        if run > max_run and soft(i):
            ln["emotion"] = "neutral"
            changed += 1
            run = 0
    limit = int(len(lines) * max_ratio)
    excited = [i for i, ln in enumerate(lines) if ln["emotion"] == "excited"]
    for i in [i for i in excited if soft(i)][::2] + [i for i in excited if soft(i)][1::2]:
        if sum(ln["emotion"] == "excited" for ln in lines) <= limit:
            break
        if lines[i]["emotion"] == "excited":
            lines[i]["emotion"] = "neutral"
            changed += 1
    return changed
