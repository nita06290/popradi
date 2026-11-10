"""放送ログの [usage] 行から、AIの使用量を Markdown の表にする（GitHub Actions のサマリー用）。

  python -m tools.usage_summary run.log
"""

import json
import sys
from pathlib import Path


def summarize(log_text: str) -> str:
    line = next((l for l in reversed(log_text.splitlines()) if l.startswith("[usage] ")), None)
    if not line:
        return "## 🤖 AIの使用量\n\n記録なし（テンプレート台本の回）\n"
    data = json.loads(line[len("[usage] "):])
    rows = ["## 🤖 AIの使用量", "", "| モデル | 呼び出し | 成功 | 入力トークン | 出力トークン | 思考トークン | エラー |", "|---|---:|---:|---:|---:|---:|---|"]
    for name, u in data["by_model"].items():
        err = ", ".join(f"{k}×{v}" for k, v in u.get("errors", {}).items()) or "-"
        rows.append(f"| {name} | {u['calls']} | {u['ok']} | {u['prompt_tokens']:,} | {u['output_tokens']:,} | {u['thinking_tokens']:,} | {err} |")
    t = data["total"]
    rows.append(f"| **合計** | {t['calls']} | {t['ok']} | {t['prompt_tokens']:,} | {t['output_tokens']:,} | {t['thinking_tokens']:,} | |")
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    print(summarize(Path(sys.argv[1]).read_text(encoding="utf-8")))
