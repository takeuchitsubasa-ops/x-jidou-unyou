"""文字数の数え方など。"""
from __future__ import annotations

import re
import unicodedata

_URL = re.compile(r"https?://\S+|\b[\w-]+\.(?:com|jp|net|co\.jp|io)/\S*", re.I)


def has_url(text: str) -> bool:
    """URL が入っているか。X API は URL 入りの投稿が高い（1件0.20ドル）ので、投稿には入れない。"""
    return bool(_URL.search(text))


def weighted_len(text: str) -> int:
    """X の数え方に近い長さ。日本語など全角は2、半角は1。"""
    n = 0
    for ch in text:
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def norm(text: str) -> str:
    """照合用に正規化する（全角→半角、空白と記号の違いを消す）。"""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[\s　*_`>#\-－ー・、。，．,.「」『』\"'“”‘’()（）\[\]【】]", "", t)
    return t.lower()
