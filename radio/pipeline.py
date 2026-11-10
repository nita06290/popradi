"""毎朝の番組生成 CLI。

例:
  python -m radio.pipeline --site site                        # 本番（RSS取得 + LLM + VOICEVOX）
  python -m radio.pipeline --site site --sample --tts windows # オフライン（サンプルRSS + Windows音声）
  python -m radio.pipeline --site site --sample --tts silent --llm template  # CI用
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from . import JST
from .audio import Segment, add_bgm, chapter_chime, clean_voice, concat, encode, ending_jingle, opening_jingle
from .episode import build_episode, build_lines, prune_and_index, seen_keys, write_episode
from .news import collect, http_fetch, sample_fetcher
from .prosody import balance_emotions
from .reading import Reader
from .research import enrich
from .script import GeminiProvider, GitHubModelsProvider, OllamaProvider, write_script, write_script_deep
from .tts import CoeiroinkTTS, SilentTTS, TTSItem, VoicevoxTTS, WindowsTTS

ROOT = Path(__file__).resolve().parent.parent
WEEKDAYS = "月火水木金土日"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def make_providers(mode: str, log) -> list:
    if mode == "template":
        return []
    providers = []
    if mode in ("auto", "gemini") and os.environ.get("GEMINI_API_KEY"):
        # GEMINI_MODEL はカンマ区切りで複数指定可（混雑・上限のとき次のモデルへ）
        for model in [m.strip() for m in os.environ.get("GEMINI_MODEL", "gemini-3.5-flash,gemini-3.5-flash-lite").split(",") if m.strip()]:
            providers.append(GeminiProvider(os.environ["GEMINI_API_KEY"], model, name=f"gemini:{model}"))
    if mode in ("auto", "github") and os.environ.get("GITHUB_TOKEN"):
        providers.append(GitHubModelsProvider(os.environ["GITHUB_TOKEN"], os.environ.get("GITHUB_MODEL", "openai/gpt-4.1-mini")))
    if mode in ("auto", "ollama") and os.environ.get("OLLAMA_URL"):
        providers.append(OllamaProvider(os.environ["OLLAMA_URL"], os.environ.get("OLLAMA_MODEL", "qwen3:4b")))
    log(f"[script] providers: {[p.name for p in providers] or ['(none)']} → template")
    return providers


def make_tts(mode: str, speakers: dict, log):
    if mode == "silent":
        return SilentTTS()
    if mode in ("windows", "sapi"):
        return WindowsTTS(speakers)
    if mode == "coeiroink":
        c = CoeiroinkTTS(speakers, os.environ.get("COEIROINK_URL", "http://127.0.0.1:50032"), log=log)
        if not c.is_alive():
            raise RuntimeError("COEIROINK に接続できません（アプリを起動してください）")
        return c
    if mode in ("voicevox", "auto"):
        v = VoicevoxTTS(speakers, os.environ.get("VOICEVOX_URL", "http://localhost:50021"), log=log)
        # CI では service コンテナの起動を最大60秒待つ。ローカルの auto は待たない
        tries = 30 if (mode == "voicevox" or os.environ.get("VOICEVOX_URL")) else 1
        for i in range(tries):
            if v.is_alive():
                return v
            if i + 1 < tries:
                time.sleep(2)
        if mode == "voicevox":
            raise RuntimeError("VOICEVOX ENGINE に接続できません")
        c = CoeiroinkTTS(speakers, os.environ.get("COEIROINK_URL", "http://127.0.0.1:50032"), log=log)
        if c.is_alive():
            log("[tts] VOICEVOX not reachable → coeiroink")
            return c
        log("[tts] VOICEVOX not reachable → windows" if sys.platform == "win32" else "[tts] → silent")
        return WindowsTTS(speakers) if sys.platform == "win32" else SilentTTS()
    raise ValueError(mode)


def sync_web(site: Path, cfg: dict | None = None) -> None:
    """web/ のアプリ本体を site/ にコピー（episodes/ と audio/ は保持）。"""
    site.mkdir(parents=True, exist_ok=True)
    for p in (ROOT / "web").rglob("*"):
        if p.is_file():
            dst = site / p.relative_to(ROOT / "web")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    cfg = cfg or load_json(ROOT / "config" / "topics.json")
    chars = load_json(ROOT / "config" / "characters.json")
    station = {
        "station_name": chars["station_name"],
        "show_title": chars["show_title"],
        "speakers": {k: {kk: v[kk] for kk in ("name", "role", "color")} for k, v in chars["speakers"].items()},
        "topics": [{"name": t["name"], "emoji": t.get("emoji", "📻"), "keywords": t["keywords"]} for t in cfg["topics"]],
    }
    (site / "station.json").write_text(json.dumps(station, ensure_ascii=False, indent=1), encoding="utf-8")


def run(args) -> dict:
    log = print
    t0 = time.time()
    now = datetime.fromisoformat(args.now).astimezone(JST) if args.now else datetime.now(JST)
    today = now.date()
    site = Path(args.site).resolve()
    cfg = load_json(Path(args.topics) if args.topics else ROOT / "config" / "topics.json")
    chars = load_json(ROOT / "config" / "characters.json")
    reader = Reader(load_json(ROOT / "config" / "reading_dict.json"))

    sync_web(site, cfg)

    # 1) ニュース収集
    fetch = sample_fetcher(ROOT / "samples" / "feeds", now) if args.sample else http_fetch
    s_urls, s_titles = seen_keys(site, 3, today)
    articles = collect(cfg, now, fetch, log=log, seen_urls=s_urls, seen_titles=s_titles)
    log(f"[news] {len(articles)} articles: " + " | ".join(f"{a.topic}:{a.title[:20]}" for a in articles))
    if not articles:
        log("[news] 記事ゼロ → 今日は休止（前日までの回はそのまま）")
        prune_and_index(site, today, args.keep_days)
        return {"status": "skipped", "reason": "no articles"}

    # 2) 台本
    date_label = f"{today.month}月{today.day}日 {WEEKDAYS[today.weekday()]}曜日"
    weekend = today.weekday() >= 5
    minutes = cfg.get("minutes", {}).get("weekend" if weekend else "weekday", 8)
    providers = make_providers(args.llm, log)
    if minutes >= 12 and providers:
        # 30分番組: Wikipedia で背景を下調べしてから、ニュースごとに章を書く
        enrich(articles, providers[0], log=log)
        script, llm_name, warnings = write_script_deep(
            articles, chars, date_label, providers, log=log, target_minutes=minutes,
            corners=cfg.get("corners"), review=not args.no_review,
        )
    else:
        script, llm_name, warnings = write_script(
            articles, chars, date_label, providers, log=log, target_minutes=minutes,
            corners=cfg.get("corners"), review=not args.no_review,
        )
    # LLMが出した固有名詞の読みを、この回だけ読み辞書に足す
    if script.get("readings"):
        reader = Reader({**load_json(ROOT / "config" / "reading_dict.json"), **{r["word"]: r["reading"] for r in script["readings"]}})
    lines = build_lines(script, reader)
    n_bal = balance_emotions(lines, max_ratio=cfg.get("max_excited_ratio", 0.3))
    if n_bal:
        log(f"[prosody] excited を {n_bal} 件おさえました")

    # 3) 音声
    tts = make_tts(args.tts, chars["speakers"], log)
    t1 = time.time()
    wavs = tts.synthesize_many([TTSItem(ln["speaker"], ln["reading"], ln["emotion"], seed=i) for i, ln in enumerate(lines)])
    if tts.name != "silent":
        wavs = [clean_voice(w) for w in wavs]
    emo = {e: sum(ln["emotion"] == e for ln in lines) for e in sorted({ln["emotion"] for ln in lines})}
    log(f"[tts] {tts.name}: {len(wavs)} lines in {time.time() - t1:.1f}s emotions={emo}")
    segments = [Segment("jingle", opening_jingle(), 0.4)]
    for i, (ln, w) in enumerate(zip(lines, wavs)):
        next_ch = lines[i + 1]["chapter"] if i + 1 < len(lines) else None
        changes = next_ch is not None and next_ch != ln["chapter"]
        segments.append(Segment("line", w, 0.5 if changes else 0.28, ref=i))
        if changes:
            segments.append(Segment("chime", chapter_chime(), 0.45))
    segments.append(Segment("jingle", ending_jingle(), 0.5))
    wav, cues, duration = concat(segments)
    bgm = chars.get("bgm", {})
    if bgm.get("enabled") and not args.no_bgm and tts.name != "silent":
        first = min(s for s, _ in cues.values()) if cues else 0.0
        wav = add_bgm(wav, list(cues.values()), start=max(0.0, first - 0.3), level_db=bgm.get("level_db", -26), duck_db=bgm.get("duck_db", -12),
                      style=args.bgm_style or bgm.get("style", "lofi"))
        log(f"[audio] BGM を追加（{args.bgm_style or bgm.get('style', 'lofi')}）")
    audio_path = encode(wav, site / "audio" / today.isoformat())

    # 4) 番組データ
    episode = build_episode(
        episode_date=today,
        script=script,
        lines=lines,
        cues=cues,
        duration=duration,
        audio_rel=audio_path.relative_to(site).as_posix(),
        articles=articles,
        characters=chars,
        generator={"llm": llm_name, "tts": tts.name, "sample": bool(args.sample)},
        warnings=warnings,
        generated_at=datetime.now(JST),
    )
    write_episode(site, episode)
    pr = prune_and_index(site, today, args.keep_days)
    result = {
        "status": "ok",
        "episode": episode["id"],
        "title": episode["title"],
        "duration_sec": duration,
        "lines": len(lines),
        "llm": llm_name,
        "tts": tts.name,
        "audio": episode["audio"],
        "audio_bytes": audio_path.stat().st_size,
        "warnings": len(warnings),
        "removed_audio": pr["removed_audio"],
        "elapsed_sec": round(time.time() - t0, 1),
    }
    log("[done] " + json.dumps(result, ensure_ascii=False))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ポプラジ 番組生成")
    ap.add_argument("--site", default="site")
    ap.add_argument("--sample", action="store_true", help="サンプルRSSでオフライン実行")
    ap.add_argument("--llm", default="auto", choices=["auto", "gemini", "github", "ollama", "template"])
    ap.add_argument("--tts", default="auto", choices=["auto", "voicevox", "coeiroink", "windows", "sapi", "silent"])
    ap.add_argument("--now", help="ISO日時で現在時刻を固定（検証用）")
    ap.add_argument("--keep-days", type=int, default=30)
    ap.add_argument("--topics", help="キーワード設定ファイル（既定: config/topics.json）")
    ap.add_argument("--no-review", action="store_true", help="LLMによる2段階目の事実チェックを省略")
    ap.add_argument("--no-bgm", action="store_true", help="BGMを入れない")
    ap.add_argument("--bgm-style", choices=["lofi", "pop", "acoustic"], help="BGMの曲調（既定: characters.json の bgm.style）")
    ap.add_argument("--web-only", action="store_true", help="アプリ本体のコピーだけ行う")
    args = ap.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if args.web_only:
        sync_web(Path(args.site).resolve(), load_json(Path(args.topics)) if args.topics else None)
        return 0
    res = run(args)
    return 0 if res["status"] in ("ok", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
