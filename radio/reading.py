"""表示用テキスト → 読み上げ用テキスト。

字幕には元の表記（OpenAI）を出し、TTSには読み（オープンエーアイ）を渡す。
"""

from __future__ import annotations

import re
import unicodedata

LETTERS = {
    "A": "エー", "B": "ビー", "C": "シー", "D": "ディー", "E": "イー", "F": "エフ", "G": "ジー",
    "H": "エイチ", "I": "アイ", "J": "ジェー", "K": "ケー", "L": "エル", "M": "エム", "N": "エヌ",
    "O": "オー", "P": "ピー", "Q": "キュー", "R": "アール", "S": "エス", "T": "ティー", "U": "ユー",
    "V": "ブイ", "W": "ダブリュー", "X": "エックス", "Y": "ワイ", "Z": "ゼット",
}
URL_RE = re.compile(r"https?://\S+")
ACRONYM_RE = re.compile(r"(?<![A-Za-z])[A-Z]{2,6}(?![a-z])")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


class Reader:
    def __init__(self, dictionary: dict[str, str]):
        items = [(k, v) for k, v in dictionary.items() if not k.startswith("_")]
        # 長い語から置換（GitHub Actions を GitHub より先に）
        self.items = sorted(items, key=lambda kv: -len(kv[0]))
        self._re = (
            re.compile("|".join(re.escape(k) for k, _ in self.items), re.IGNORECASE) if self.items else None
        )
        self._map = {k.lower(): v for k, v in self.items}

    def __call__(self, text: str) -> str:
        t = unicodedata.normalize("NFKC", text)
        t = URL_RE.sub("リンク", t)
        t = EMOJI_RE.sub("", t)
        if self._re:
            t = self._re.sub(lambda m: self._map[m.group(0).lower()], t)
        t = ACRONYM_RE.sub(lambda m: "".join(LETTERS[c] for c in m.group(0)), t)
        t = t.replace("〜", "ー").replace("…", "、")
        t = re.sub(r"[「」『』【】]", "", t)
        return re.sub(r"\s+", " ", t).strip()
