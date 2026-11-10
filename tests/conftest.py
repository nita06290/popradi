import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radio import JST  # noqa: E402
from radio.news import Article  # noqa: E402

NOW = datetime(2026, 11, 8, 5, 0, tzinfo=JST)


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def characters():
    return json.loads((ROOT / "config" / "characters.json").read_text(encoding="utf-8"))


@pytest.fixture
def topics_cfg():
    return json.loads((ROOT / "config" / "topics.json").read_text(encoding="utf-8"))


def make_article(title, hours_ago=1, url=None, desc="", topic="生成AI", source="テスト新聞", aid=None):
    from datetime import timedelta

    a = Article(
        title=title,
        url=url or f"https://example.com/{abs(hash(title))}",
        source=source,
        published_at=NOW - timedelta(hours=hours_ago),
        description=desc,
        topic=topic,
    )
    a.id = aid or f"n{abs(hash(title)) % 10**8:08d}"
    return a


@pytest.fixture
def articles():
    return [
        make_article("架空社が生成AIを全社導入", desc="社員3000人に導入し、作業時間が20%短縮した。", aid="n00000001"),
        make_article("Azureの新リージョン開設へ", desc="国内のデータ保管ニーズに対応。", topic="クラウド", aid="n00000002"),
        make_article("Python入門講座を無料公開", desc="", topic="開発技術", aid="n00000003"),
    ]
