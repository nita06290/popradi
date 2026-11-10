"""番組データ（JSON / VTT）の組み立てと、アーカイブ索引・古い音声の整理。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .news import Article
from .prosody import resolve_emotion


def build_lines(script: dict, reader) -> list[dict]:
    lines = []
    for ci, ch in enumerate(script["chapters"]):
        for ln in ch["lines"]:
            text = str(ln["text"]).strip()
            lines.append({"speaker": ln["speaker"], "text": text, "reading": reader(text), "emotion": resolve_emotion(text, ln.get("emotion")), "chapter": ci})
    return lines


def build_episode(
    *,
    episode_date: date,
    script: dict,
    lines: list[dict],
    cues: dict[int, tuple[float, float]],
    duration: float,
    audio_rel: str | None,
    articles: list[Article],
    characters: dict,
    generator: dict,
    warnings: list[str],
    generated_at: datetime,
) -> dict:
    for i, ln in enumerate(lines):
        ln["start"], ln["end"] = cues.get(i, (0.0, 0.0))
    chapters = []
    for ci, ch in enumerate(script["chapters"]):
        first = next((ln for ln in lines if ln["chapter"] == ci), None)
        c = {"title": ch.get("title", f"チャプター{ci + 1}"), "start": first["start"] if first else 0.0, "source_ids": ch.get("source_ids", [])}
        for k in ("corner", "event"):
            if ch.get(k):
                c[k] = ch[k]
        chapters.append(c)
    topics_by_id = {a.id: a.topic for a in articles}
    for c in chapters:
        c["topic"] = next((topics_by_id[s] for s in c["source_ids"] if s in topics_by_id), "")
    return {
        "id": episode_date.isoformat(),
        "date": episode_date.isoformat(),
        "title": str(script.get("title", "")).strip() or f"{episode_date}の放送",
        "summary": str(script.get("summary", "")).strip(),
        "audio": audio_rel,
        "duration": duration,
        "generated_at": generated_at.isoformat(),
        "generator": generator,
        "speakers": {
            k: {"name": v["name"], "role": v["role"], "color": v["color"], "credit": (v.get(generator.get("tts", "")) or {}).get("credit", "")}
            for k, v in characters["speakers"].items()
        },
        "chapters": chapters,
        "lines": lines,
        "sources": [
            {"id": a.id, "title": a.title, "source": a.source, "url": a.url, "topic": a.topic, "published_at": a.published_at.isoformat()}
            for a in articles
        ],
        "readings": script.get("readings", []),
        "qa": {"warnings": warnings},
    }


def fmt_vtt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_vtt(episode: dict) -> str:
    out = ["WEBVTT", ""]
    for i, ln in enumerate(episode["lines"], 1):
        name = episode["speakers"][ln["speaker"]]["name"]
        out += [str(i), f"{fmt_vtt_time(ln['start'])} --> {fmt_vtt_time(ln['end'])}", f"<v {name}>{ln['text']}", ""]
    return "\n".join(out)


def write_episode(site: Path, episode: dict) -> None:
    ep_dir = site / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / f"{episode['id']}.json").write_text(json.dumps(episode, ensure_ascii=False, indent=1), encoding="utf-8")
    (ep_dir / f"{episode['id']}.vtt").write_text(to_vtt(episode), encoding="utf-8")


def summary_of(ep: dict) -> dict:
    return {
        "id": ep["id"],
        "date": ep["date"],
        "title": ep["title"],
        "summary": ep.get("summary", ""),
        "duration": ep.get("duration", 0),
        "has_audio": bool(ep.get("audio")),
        "topics": sorted({c["topic"] for c in ep.get("chapters", []) if c.get("topic")}),
        "news_count": len(ep.get("sources", [])),
    }


def prune_and_index(site: Path, today: date, keep_days: int = 30) -> dict:
    """keep_days より古い回の音声を削除（台本JSONは残す）し、episodes/index.json を再生成。"""
    ep_dir = site / "episodes"
    cutoff = today - timedelta(days=keep_days)
    summaries = []
    removed = []
    for p in sorted(ep_dir.glob("*.json")):
        if p.name == "index.json":
            continue
        ep = json.loads(p.read_text(encoding="utf-8"))
        if date.fromisoformat(ep["date"]) < cutoff and ep.get("audio"):
            audio = site / ep["audio"]
            if audio.exists():
                audio.unlink()
            removed.append(ep["audio"])
            ep["audio"] = None
            p.write_text(json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
        summaries.append(summary_of(ep))
    summaries.sort(key=lambda s: s["date"], reverse=True)
    index = {"updated": today.isoformat(), "episodes": summaries}
    (ep_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"removed_audio": removed, "count": len(summaries)}


def seen_urls(site: Path, days: int = 3, today: date | None = None) -> set[str]:
    """直近の回で紹介済みの記事URL（同じニュースを翌日も流さない）。"""
    return seen_keys(site, days, today)[0]


def seen_keys(site: Path, days: int = 3, today: date | None = None) -> tuple[set[str], list[str]]:
    """直近の回で紹介済みの (正規化URL集合, 正規化見出しリスト)。別媒体の同じニュースも弾くため見出しも使う。"""
    from .news import canonical_url, normalize_title

    today = today or date.today()
    urls: set[str] = set()
    titles: list[str] = []
    for p in (site / "episodes").glob("*.json"):
        if p.name == "index.json":
            continue
        try:
            ep = json.loads(p.read_text(encoding="utf-8"))
            d = date.fromisoformat(ep["date"])
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if today - timedelta(days=days) <= d < today:
            urls.update(canonical_url(s["url"]) for s in ep.get("sources", []))
            titles.extend(normalize_title(s["title"], s.get("source", "")) for s in ep.get("sources", []))
    return urls, titles
