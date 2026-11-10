"""ニュース収集: RSS取得 → パース → 期間フィルタ → 重複排除 → ランキング。

記事本文はスクレイピングしない（著作権・ペイウォール配慮）。
使うのは見出し・RSSの説明文・媒体名・URL・公開日時だけ。
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from . import JST

TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|ref$|ref_src|oc$)")
TAG_RE = re.compile(r"<[^>]+>")
# 見出しに付く飾り: 「＜画像1 / 3＞」「｜Page 3」「(写真3枚)」など
TITLE_JUNK = [
    re.compile(r"^[＜<]?\s*画像\s*\d+\s*/\s*\d+\s*[＞>]\s*"),
    re.compile(r"\s*[｜|]\s*Page\s*\d+\s*$", re.I),
    re.compile(r"\s*[（(]写真\d+枚[）)]\s*$"),
    re.compile(r"^(写真|画像)\s*\d+\s*/\s*\d+\s*[｜|]\s*"),
]
SPACE_RE = re.compile(r"\s+")


@dataclass
class Article:
    title: str
    url: str
    source: str
    published_at: datetime
    description: str = ""
    topic: str = ""
    id: str = ""
    score: float = 0.0
    keywords_hit: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        return d


def google_news_url(keyword: str) -> str:
    q = quote(f"{keyword} when:1d")
    return f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(TAG_RE.sub(" ", html.unescape(s)))
    return SPACE_RE.sub(" ", s).strip()


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)]
    netloc = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", netloc, path, urlencode(query), ""))


def clean_title(title: str) -> str:
    for r in TITLE_JUNK:
        title = r.sub("", title)
    return title.strip()


def normalize_title(title: str, source: str = "") -> str:
    t = unicodedata.normalize("NFKC", title)
    # Google News は「見出し - 媒体名」形式なので媒体名を落とす
    if source and t.endswith(f" - {unicodedata.normalize('NFKC', source)}"):
        t = t[: -(len(source) + 3)]
    t = re.sub(r"[\s　「」『』【】\[\]()（）、。,.!！?？:：|｜・\-ー―]+", "", t)
    return t.lower()


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def parse_rss(xml_text: str, default_source: str = "") -> list[Article]:
    """RSS 2.0 / Atom をパースする（feedparser 非依存）。壊れたXMLは空リスト。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[Article] = []
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    for it in items:
        def g(tag: str) -> str | None:
            el = it.find(tag)
            if el is None:
                el = it.find(f"{atom}{tag}")
            if el is None:
                return None
            if tag == "link" and el.text is None:
                return el.get("href")
            return el.text

        title = clean_text(g("title"))
        url = (g("link") or "").strip()
        if not title or not url:
            continue
        src_el = it.find("source")
        source = clean_text(src_el.text) if src_el is not None and src_el.text else default_source
        published = parse_date(g("pubDate") or g("published") or g("updated"))
        if published is None:
            continue
        desc = clean_text(g("description") or g("summary"))
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)]
        title = clean_title(title)
        # Google News の description は「見出し + 媒体名」の繰り返しなので、見出しを含むなら捨てる
        nt, nd = normalize_title(title, source), normalize_title(desc, source)
        if not nd or nt == nd or (len(nt) >= 8 and nt[: max(8, len(nt) * 2 // 3)] in nd):
            desc = ""
        out.append(Article(title=title, url=url, source=source or "不明", published_at=published, description=desc))
    return out


def within_window(a: Article, now: datetime, hours: float) -> bool:
    return now - timedelta(hours=hours) <= a.published_at <= now + timedelta(minutes=10)


# 重複判定のときに無視する、どの記事にも出てくる語（これで「東京の写真展」同士を同一視しない）
GENERIC_WORDS = sorted(
    ["東京", "都内", "開催中", "開催", "写真展", "美術館", "展覧会", "企画展", "展示", "アート", "イベント", "紹介", "公開",
     "発表", "開幕", "スタート", "始まった", "注目", "記念", "特別展", "レポート", "連載", "ニュース"],
    key=len, reverse=True,
)


def _bigrams(s: str) -> set[str]:
    for w in GENERIC_WORDS:
        s = s.replace(w, "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def is_similar(a: str, b: str, threshold: float = 0.8, overlap: float = 0.4) -> bool:
    """正規化済みの見出し同士が同じニュースか。

    言い回しが違う別媒体の記事（「日中台100年を紹介 東京・汐留で報道写真展」と
    「日中台１００年紹介、報道写真展 東京・汐留シオサイト」）も拾えるよう、
    ありふれた語を除いた2文字単位の重なり（小さい方に対する割合）でも判定する。
    """
    if not a or not b:
        return False
    if a == b or (len(a) > 12 and (a in b or b in a)):
        return True
    if SequenceMatcher(None, a, b).ratio() >= threshold:
        return True
    ba, bb = _bigrams(a), _bigrams(b)
    if min(len(ba), len(bb)) < 4:
        return False
    return len(ba & bb) / min(len(ba), len(bb)) >= overlap


def dedupe(articles: Iterable[Article], seen_urls: set[str] | None = None, seen_titles: Iterable[str] = ()) -> list[Article]:
    """URL正規化 + 見出し類似度で重複排除。先に来た（=スコアが高い）ものを残す。

    seen_urls / seen_titles には過去の放送で紹介済みのもの（正規化済み）を渡す。
    """
    seen_urls = set(seen_urls or ())
    kept: list[Article] = []
    kept_titles: list[str] = list(seen_titles)
    for a in articles:
        cu = canonical_url(a.url)
        nt = normalize_title(a.title, a.source)
        if cu in seen_urls or any(is_similar(nt, t) for t in kept_titles):
            continue
        seen_urls.add(cu)
        kept_titles.append(nt)
        kept.append(a)
    return kept


def relevance_terms(topic: dict) -> list[str]:
    """関連度判定に使う語。'美術展 東京' のような複数語キーワードは語に分け、地域の語は除く。"""
    region = set(topic.get("region_any", []))
    terms: list[str] = []
    for kw in topic.get("keywords", []):
        for tok in kw.split():
            if tok not in region and tok not in terms:
                terms.append(tok)
    return terms


def in_region(a: Article, topic: dict) -> bool:
    region = topic.get("region_any")
    if not region:
        return True
    text = f"{a.title} {a.description}"
    if any(x in text for x in topic.get("exclude_any", [])) and not any(x in a.title for x in region):
        return False
    return any(x in text for x in region)


def score_article(a: Article, keywords: list[str], now: datetime) -> float:
    text = f"{a.title} {a.description}".lower()
    hits = [k for k in keywords if k.lower() in text]
    a.keywords_hit = hits
    title_hits = sum(1 for k in keywords if k.lower() in a.title.lower())
    age_h = max(0.0, (now - a.published_at).total_seconds() / 3600)
    recency = max(0.0, 1.0 - age_h / 30)
    return title_hits * 2.0 + len(hits) + recency * 1.5 + (0.5 if a.description else 0)


def article_id(a: Article) -> str:
    return "n" + hashlib.sha1(canonical_url(a.url).encode()).hexdigest()[:8]


def select_articles(
    by_topic: dict[str, list[Article]],
    topics: list[dict],
    now: datetime,
    lookback_hours: float = 30,
    max_per_topic: int = 2,
    max_total: int = 6,
    seen_urls: set[str] | None = None,
    seen_titles: Iterable[str] = (),
) -> list[Article]:
    """トピックごとにスコア順で選び、全体で重複排除して上限件数に絞る。"""
    seen_titles = list(seen_titles)
    keywords = {t["name"]: relevance_terms(t) for t in topics}
    picked: list[Article] = []
    global_seen = set(seen_urls or ())
    for t in topics:
        name = t["name"]
        cands = [a for a in by_topic.get(name, []) if within_window(a, now, t.get("lookback_hours", lookback_hours)) and in_region(a, t)]
        for a in cands:
            a.topic = name
            a.score = score_article(a, keywords[name], now)
        cands = [a for a in cands if a.keywords_hit]
        cands.sort(key=lambda a: (-a.score, -a.published_at.timestamp()))
        uniq = dedupe(cands, global_seen, seen_titles)
        # 他トピックで既に選んだ記事と見出しが似ていれば除外
        uniq = [a for a in uniq if not any(is_similar(normalize_title(a.title, a.source), normalize_title(p.title, p.source)) for p in picked)]
        chosen = uniq[: t.get("max", max_per_topic)]
        global_seen.update(canonical_url(a.url) for a in chosen)
        picked.extend(chosen)
    # トピックを巡回しながら上限まで（偏り防止）
    result: list[Article] = []
    queues = {t["name"]: [a for a in picked if a.topic == t["name"]] for t in topics}
    while len(result) < max_total and any(queues.values()):
        for t in topics:
            q = queues[t["name"]]
            if q and len(result) < max_total:
                result.append(q.pop(0))
    for a in result:
        a.id = article_id(a)
    return result


def assign_topic(a: Article, topics: list[dict]) -> str | None:
    text = f"{a.title} {a.description}".lower()
    for t in topics:
        if any(k.lower() in text for k in t.get("keywords", [])):
            return t["name"]
    return None


Fetcher = Callable[[str], str]


def collect(
    config: dict, now: datetime, fetch: Fetcher, log=print, seen_urls: set[str] | None = None, seen_titles: Iterable[str] = ()
) -> list[Article]:
    """設定に従って全トピックを取得。取得失敗したフィードはスキップして続行する。"""
    topics = config["topics"]
    by_topic: dict[str, list[Article]] = {t["name"]: [] for t in topics}
    for t in topics:
        for kw in t.get("keywords", []):
            url = google_news_url(kw)
            try:
                by_topic[t["name"]].extend(parse_rss(fetch(url)))
            except Exception as e:  # noqa: BLE001 - 1フィードの失敗で全体を止めない
                log(f"[news] skip {kw}: {e}")
    for feed in config.get("extra_feeds", []):
        try:
            for a in parse_rss(fetch(feed["url"]), default_source=feed["name"]):
                name = assign_topic(a, topics)
                if name:
                    by_topic[name].append(a)
        except Exception as e:  # noqa: BLE001
            log(f"[news] skip feed {feed['name']}: {e}")
    return select_articles(
        by_topic,
        topics,
        now,
        lookback_hours=config.get("lookback_hours", 30),
        max_per_topic=config.get("max_per_topic", 2),
        max_total=config.get("max_total", 6),
        seen_urls=seen_urls,
        seen_titles=seen_titles,
    )


def http_fetch(url: str, timeout: float = 20) -> str:
    import requests

    r = requests.get(url, timeout=timeout, headers={"User-Agent": "PopRadio/0.1 (personal RSS reader)"})
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


HOURS_AGO_RE = re.compile(r"\{\{HOURS_AGO:(\d+(?:\.\d+)?)\}\}")


def sample_fetcher(sample_dir: Path, now: datetime) -> Fetcher:
    """オフライン検証用: URLに含まれるキーワードに対応するサンプルRSSを返す。

    サンプル内の {{HOURS_AGO:n}} は now から n 時間前の RFC822 日時に置換する。
    """
    from email.utils import format_datetime
    from urllib.parse import unquote

    def fetch(url: str) -> str:
        decoded = unquote(url)
        for p in sorted(sample_dir.glob("*.xml")):
            if p.stem in decoded:
                text = p.read_text(encoding="utf-8")
                return HOURS_AGO_RE.sub(lambda m: format_datetime(now - timedelta(hours=float(m.group(1)))), text)
        raise FileNotFoundError(f"no sample for {decoded}")

    return fetch
