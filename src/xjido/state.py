"""data/ の中の記録の読み書き。Actions がこれをコミットして残す。"""
from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT

DATA = ROOT / "data"
QUEUE = DATA / "予約.json"          # 投稿の列（予約・確認待ち・投稿済み・失敗）
POSTED = DATA / "投稿記録.jsonl"      # 投稿した物（1スレッド1行）
METRICS = DATA / "数字.jsonl"         # 取ってきた投稿の数字（取るたびに1投稿1行を足す）
ACCOUNT = DATA / "アカウント.json"    # 自分のユーザーID（毎回取ると料金がかかるので残す）


def load_queue(path: Path = QUEUE) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"items": []}


def save_queue(q: dict, path: Path = QUEUE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(q, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def user_id(client=None) -> str:
    if ACCOUNT.exists():
        return json.loads(ACCOUNT.read_text(encoding="utf-8"))["id"]
    me = client.me()
    ACCOUNT.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNT.write_text(json.dumps({"id": me["id"], "username": me.get("username")}, ensure_ascii=False) + "\n", encoding="utf-8")
    return me["id"]
