"""設定.yml の読み込み。足りない項目は既定値で補う。"""
from __future__ import annotations

import copy
import os
from datetime import timedelta, timezone
from pathlib import Path

import yaml

# 作業の場所。ふだんはこのリポジトリ。別のフォルダ（設定.yml・メモリ・data を置いた所）で試す時だけ、環境変数 XJIDO_ROOT で変える
ROOT = Path(os.environ.get("XJIDO_ROOT") or Path(__file__).resolve().parents[2])
JST = timezone(timedelta(hours=9))

DEFAULTS = {
    "アカウント名": "",
    "投稿": {
        "時刻": ["06:00", "12:00", "18:00"],
        "スレッドの長さ": 3,
        "1投稿の最大文字数": 280,
        "遅れの許容_分": 180,
        "まとめるの最大_1日": 1,
    },
    "確認": {
        "方式": "止まった時だけ確認",
        "確認待ちの最大件数_1日": 3,
        "AIで二重に確かめる": True,
        "OKラベル": "OK",
        "予備の案の数": 2,
    },
    "AI": {
        "提供元": "anthropic",
        "モデル": "claude-sonnet-5-5",
        "確認用のモデル": "claude-sonnet-5-5",
        "分析のモデル": "claude-sonnet-5-5",
    },
    "数字を取る": {"日数": 3, "最大件数": 60},
    "分析": {"日数": 14, "最大件数": 200},
}

MODES = ("止まった時だけ確認", "全部確認する", "止まった案は捨てる")
# 前の名前でも動くようにする
OLD_MODES = {"数字がある時だけ": "止まった時だけ確認", "しない": "止まった案は捨てる"}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "設定.yml"
    over = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg = _merge(DEFAULTS, over or {})
    cfg["確認"]["方式"] = OLD_MODES.get(cfg["確認"]["方式"], cfg["確認"]["方式"])
    if cfg["確認"]["方式"] not in MODES:
        raise ValueError(f"設定.yml の「確認: 方式」は {MODES} のどれかにしてください（今: {cfg['確認']['方式']}）")
    for t in cfg["投稿"]["時刻"]:
        h, m = str(t).split(":")
        if not (0 <= int(h) < 24 and 0 <= int(m) < 60):
            raise ValueError(f"設定.yml の投稿の時刻が正しくありません: {t}")
    return cfg
