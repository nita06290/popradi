"""COEIROINK の声を一覧・設定・試聴するツール（COEIROINK v2 を起動した状態で使う）。

  python -m tools.coeiroink_speakers                          # ダウンロード済みキャラとスタイルの一覧
  python -m tools.coeiroink_speakers --set sora=KANA:のーまる   # ソラの声を設定（characters.json を更新）
  python -m tools.coeiroink_speakers --set sora=KANA:のーまる --emotion sora:excited=ないしょばなし
  python -m tools.coeiroink_speakers --preview                 # 2人の試聴用WAVを preview/ に書き出す
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radio.tts import CoeiroinkTTS, TTSError, TTSItem  # noqa: E402

CHAR_PATH = ROOT / "config" / "characters.json"
PREVIEW_LINES = {
    "haru": ("おはようございます！今日も気になるニュースを、わかりやすく紹介していきますね。", "neutral"),
    "sora": ("ええっ、それってどういうことなんですか？", "question"),
}


def find_style(speakers: list[dict], spec: str) -> tuple[dict, dict]:
    """'キャラ名:スタイル名'（スタイル省略時は最初のスタイル）から (speaker, style) を返す。"""
    name, _, style_name = spec.partition(":")
    sp = next((s for s in speakers if s["speakerName"] == name), None)
    if sp is None:
        raise SystemExit(f"キャラ '{name}' が見つかりません。候補: {', '.join(s['speakerName'] for s in speakers)}")
    if not sp["styles"]:
        raise SystemExit(f"{name} にスタイルがありません")
    st = next((x for x in sp["styles"] if x["styleName"] == style_name), None) if style_name else sp["styles"][0]
    if st is None:
        raise SystemExit(f"{name} にスタイル '{style_name}' がありません。候補: {', '.join(x['styleName'] for x in sp['styles'])}")
    return sp, st


def apply_settings(chars: dict, speakers: list[dict], sets: list[str], emotions: list[str]) -> list[str]:
    msgs = []
    for s in sets:
        who, _, spec = s.partition("=")
        if who not in chars["speakers"]:
            raise SystemExit(f"'{who}' は haru / sora のどちらかを指定してください")
        sp, st = find_style(speakers, spec)
        cfg = chars["speakers"][who].setdefault("coeiroink", {})
        cfg.update(speaker_uuid=sp["speakerUuid"], style_id=st["styleId"], credit=f"COEIROINK:{sp['speakerName']}", styles={})
        cfg.pop("_comment", None)
        msgs.append(f"{chars['speakers'][who]['name']} → {sp['speakerName']}（{st['styleName']}）")
    for e in emotions:
        who_emo, _, style_name = e.partition("=")
        who, _, emo = who_emo.partition(":")
        cfg = chars["speakers"][who]["coeiroink"]
        sp = next(s for s in speakers if s["speakerUuid"] == cfg["speaker_uuid"])
        _, st = find_style(speakers, f"{sp['speakerName']}:{style_name}")
        cfg.setdefault("styles", {})[emo] = st["styleId"]
        msgs.append(f"{chars['speakers'][who]['name']} の {emo} → {st['styleName']}")
    return msgs


def main(argv=None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="COEIROINK の声を一覧・設定・試聴")
    ap.add_argument("--set", action="append", default=[], metavar="haru|sora=キャラ名:スタイル名")
    ap.add_argument("--emotion", action="append", default=[], metavar="sora:excited=スタイル名")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--url", default=os.environ.get("COEIROINK_URL", "http://127.0.0.1:50032"))
    args = ap.parse_args(argv)

    chars = json.loads(CHAR_PATH.read_text(encoding="utf-8"))
    tts = CoeiroinkTTS(chars["speakers"], args.url)
    if not tts.is_alive():
        print(f"COEIROINK に接続できません（{args.url}）。アプリを起動してから実行してください。")
        return 1
    speakers = tts.list_speakers()

    if args.set or args.emotion:
        for m in apply_settings(chars, speakers, args.set, args.emotion):
            print("✔", m)
        CHAR_PATH.write_text(json.dumps(chars, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"→ {CHAR_PATH.relative_to(ROOT)} を更新しました")
    elif not args.preview:
        print("ダウンロード済みのキャラクター:")
        for s in speakers:
            print(f"  {s['speakerName']}: " + " / ".join(st["styleName"] for st in s["styles"]))
        for k, v in chars["speakers"].items():
            cur = v.get("coeiroink", {}).get("speaker_uuid")
            sp = next((s for s in speakers if s["speakerUuid"] == cur), None)
            print(f"現在の{v['name']}: {sp['speakerName'] if sp else '未設定'}")
        print("\n設定例: python -m tools.coeiroink_speakers --set sora=キャラ名:スタイル名")

    if args.preview:
        tts = CoeiroinkTTS(chars["speakers"], args.url)
        out = ROOT / "preview"
        out.mkdir(exist_ok=True)
        try:
            wavs = tts.synthesize_many([TTSItem(k, t, e) for k, (t, e) in PREVIEW_LINES.items()])
        except TTSError as e:
            print(e)
            return 1
        for k, w in zip(PREVIEW_LINES, wavs):
            (out / f"coeiroink_{k}.wav").write_bytes(w)
            print("♪", out / f"coeiroink_{k}.wav")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
