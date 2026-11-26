"""コマンド：  python -m xjido {morning|post|weekly} [--dry-run]"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import analyze, config, morning, publish
from .llm import LLMWait
from .config import JST


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="xjido", description="X運用を自動で回す仕組み")
    ap.add_argument("command", choices=["morning", "post", "weekly"], help="morning=朝の準備／post=投稿／weekly=週の分析")
    ap.add_argument("--dry-run", action="store_true", help="X に投稿せず、データも書き換えず、出力/ に書くだけ")
    ap.add_argument("--no-x", action="store_true", help="X API を呼ばない（数字を取らない）")
    ap.add_argument("--no-github", action="store_true", help="GitHub の Issue を読み書きしない")
    ap.add_argument("--provider", choices=["anthropic", "claude-cli", "file"], help="設定.yml の「AI: 提供元」を、この実行だけ変える")
    ap.add_argument("--model", help="設定.yml の「AI: モデル」を、この実行だけ変える")
    ap.add_argument("--now", help="今の日時を指定する（日本時間。例 2026-11-27T04:50）。試す時だけ")
    ap.add_argument("--config", help="設定ファイルの場所")
    a = ap.parse_args(argv)

    cfg = config.load(a.config)
    if a.provider:
        cfg["AI"]["提供元"] = a.provider
    if a.model:
        cfg["AI"]["モデル"] = a.model
    now = datetime.fromisoformat(a.now).replace(tzinfo=JST) if a.now else datetime.now(JST)

    try:
        if a.command == "morning":
            text = morning.run(cfg, dry=a.dry_run, no_x=a.no_x, no_github=a.no_github, now=now)
            print(text)
            return 0
        if a.command == "post":
            return publish.run(cfg, dry=a.dry_run, no_github=a.no_github, now=now)
        return _weekly(cfg, a, now)
    except LLMWait as w:  # 提供元 file：返事のファイルを置いてから、同じコマンドをもう一度
        print("返事のファイルを待っています。次の場所に返事を置いてから、同じコマンドをもう一度動かしてください：")
        for p in w.paths:
            print("  " + p)
        return 3


def _weekly(cfg, a, now):
    return analyze.run(cfg, dry=a.dry_run, no_x=a.no_x, now=now)


if __name__ == "__main__":
    sys.exit(main())
