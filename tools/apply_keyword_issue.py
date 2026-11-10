"""GitHub Issue フォーム（.github/ISSUE_TEMPLATE/keywords.yml）の本文から config/topics.json を更新する。

スマホの GitHub アプリから Issue を立てるだけでキーワードを変えられるようにするためのもの。

  python -m tools.apply_keyword_issue issue_body.md [config/topics.json]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_form(body: str) -> dict[str, str]:
    """'### 見出し\\n\\n値' 形式の Issue フォーム本文を {見出し: 値} にする。"""
    out: dict[str, str] = {}
    for m in re.finditer(r"^###\s*(.+?)\s*\n(.*?)(?=^###\s|\Z)", body, re.S | re.M):
        v = m.group(2).strip()
        out[m.group(1).strip()] = "" if v == "_No response_" else v
    return out


def split_keywords(s: str) -> list[str]:
    return [k.strip() for k in re.split(r"[,\n、，]+", s) if k.strip()][:10]


def apply(cfg: dict, form: dict[str, str]) -> str:
    action = form.get("操作", "")
    name = form.get("トピック名", "").strip()[:30]
    if not name:
        raise ValueError("トピック名が空です")
    topics = cfg.setdefault("topics", [])
    cur = next((t for t in topics if t["name"] == name), None)
    if action.startswith("削除"):
        if not cur:
            raise ValueError(f"トピック「{name}」はありません")
        if len(topics) == 1:
            raise ValueError("最後のトピックは削除できません")
        topics.remove(cur)
        return f"🗑 トピック「{name}」を削除しました"
    kws = split_keywords(form.get("キーワード", ""))
    if not kws:
        raise ValueError("キーワードが空です")
    emoji = (form.get("絵文字", "") or "📻").strip()[:4]
    if cur:
        cur["keywords"] = kws
        cur["emoji"] = emoji or cur.get("emoji", "📻")
        return f"✏️ 「{name}」のキーワードを {', '.join(kws)} に変更しました"
    if len(topics) >= 8:
        raise ValueError("トピックは8個までです")
    topics.append({"name": name, "emoji": emoji, "keywords": kws})
    return f"✨ トピック「{name}」（{', '.join(kws)}）を追加しました"


def main(argv: list[str]) -> int:
    body = Path(argv[1]).read_text(encoding="utf-8")
    cfg_path = Path(argv[2]) if len(argv) > 2 else ROOT / "config" / "topics.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    try:
        msg = apply(cfg, parse_form(body))
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(msg)
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv))
