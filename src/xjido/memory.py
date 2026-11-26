"""メモリ（リポジトリの中の md）の読み書き。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import JST, ROOT

MEM = ROOT / "メモリ"
KATA = MEM / "伸びる型.md"
NETA = MEM / "ネタ帳.md"
FACTS = MEM / "会社の事実.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def corpus_text() -> str:
    """投稿に使ってよい元の文章。ネタ帳と会社の事実だけ（伸びる型は含めない）。"""
    neta = re.sub(r"(?m)^(##\s+)\d{4}-\d{2}(?:-\d{2})?\s*", r"\1", read(NETA))  # 見出しの日付は、数字として照合しない
    return neta + "\n\n" + read(FACTS) + "\n\n" + theme_material()


SAMPLE_MARK = "【見本】"


def is_sample() -> bool:
    """メモリが、配られた見本のままか（見本のまま本番で投稿しないための見張り）。"""
    return SAMPLE_MARK in strip_comments(read(NETA) + read(FACTS) + read(THEME))


def theme_material() -> str:
    """テーマ.md も本人の入力なので、投稿の材料に入れる（「使ったテーマ」の行頭の日付は、数字として照合しない）。"""
    t = re.sub(r"(?m)^(\s*[-*・]\s*)\d{4}-\d{2}-\d{2}\s+", r"\1", read(THEME))
    return t.replace(SAMPLE_MARK, "")


def strip_comments(t: str) -> str:
    return re.sub(r"<!--.*?-->", "", t, flags=re.S)


def neta_titles() -> list[str]:
    return re.findall(r"(?m)^##\s+(.+)$", read(NETA))


def append_neta(title: str, body: str, source: str, now: datetime | None = None, path: Path = NETA) -> str:
    """ネタ帳の終わりに1件足す。"""
    now = now or datetime.now(JST)
    title = re.sub(r"\s+", " ", title).strip() or "（題なし）"
    body = body.strip()
    block = f"\n## {now:%Y-%m-%d} {title}\n- 出どころ：{source}\n\n{body}\n"
    text = read(path)
    path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
    return block


# ---- お手本（本人の伸びたスレッドの全文）と、テーマ（今日書いてほしい話の1行） ----

OTEHON = MEM / "お手本.md"
THEME = MEM / "テーマ.md"
AUTO_HEAD = "## 伸びたスレッド（毎週日曜に自動で入れ替わります）"
KEEP_HEAD = "## 手で残すお手本（自動では変わりません）"
UNUSED_HEAD = "## まだ使っていないテーマ"
USED_HEAD = "## 使ったテーマ"


def parse_otehon(text: str | None = None) -> list[dict]:
    """お手本.md を読んで、スレッドごとに {"head": 見出し, "views": 表示数, "url", "posts": [..]} を返す。"""
    text = strip_comments(read(OTEHON) if text is None else text)
    out = []
    for blk in re.split(r"(?m)^###\s+", text)[1:]:
        head, _, rest = blk.partition("\n")
        parts = re.split(r"(?m)^\s*\d+つ目[：:]\s*$", rest)
        posts = [re.sub(r"\n{3,}", "\n\n", p.split("\n## ")[0]).strip() for p in parts[1:]]
        posts = [p for p in posts if p]
        m = re.search(r"([\d,]+)\s*表示", head)
        u = re.search(r"https?://\S+", head)
        out.append({"head": head.strip(), "views": int(m.group(1).replace(",", "")) if m else 0, "url": u.group(0) if u else "", "posts": posts})
    return out


def otehon_avg_len() -> int | None:
    """お手本の1投稿の平均の字数（全角で数えた字数）。無ければ None。"""
    from .textutil import weighted_len

    posts = [p for t in parse_otehon() for p in t["posts"]]
    if not posts:
        return None
    return round(sum(weighted_len(p) for p in posts) / len(posts) / 2)


def otehon_for_prompt() -> str:
    """生成の依頼に渡すお手本（見出しの表示数・URL は省き、投稿の本文だけ）。"""
    blocks = []
    for i, t in enumerate(parse_otehon(), 1):
        if not t["posts"]:
            continue
        body = "\n".join(f"{j}つ目：{p}" for j, p in enumerate(t["posts"], 1))
        blocks.append(f"［お手本{i}］\n{body}")
    return "\n\n".join(blocks)


def _theme_line(ln: str) -> str | None:
    m = re.match(r"^\s*[-*・]\s*(.+?)\s*$", ln)
    if not m:
        return None
    return m.group(1).replace(SAMPLE_MARK, "").strip() or None


def unused_themes(text: str | None = None) -> list[str]:
    """テーマ.md の「まだ使っていないテーマ」の行（上から順）。"""
    text = strip_comments(read(THEME) if text is None else text)
    sec = text.split(UNUSED_HEAD, 1)
    if len(sec) < 2:
        return []
    body = sec[1].split("\n## ", 1)[0]
    return [t for t in (_theme_line(ln) for ln in body.splitlines()) if t]


def add_theme(theme: str, path: Path | None = None) -> None:
    """「まだ使っていないテーマ」の終わりに1行足す（スマホの Issue から来た物も、ここに入る）。"""
    path = path or THEME
    theme = re.sub(r"\s+", " ", theme).strip()
    text = read(path) or f"# テーマ\n\n{UNUSED_HEAD}\n\n{USED_HEAD}\n"
    if UNUSED_HEAD not in text:
        text = text.rstrip("\n") + f"\n\n{UNUSED_HEAD}\n"
    head, rest = text.split(UNUSED_HEAD, 1)
    body, sep, tail = rest.partition("\n## ")
    body = body.rstrip("\n") + f"\n- {theme}\n"
    path.write_text(head + UNUSED_HEAD + body + ("\n## " + tail if sep else ""), encoding="utf-8")


def mark_themes_used(themes: list[str], now: datetime | None = None, path: Path | None = None) -> None:
    """使ったテーマを「まだ使っていないテーマ」から消し、「使ったテーマ」に日付を付けて移す。"""
    path = path or THEME
    if not themes or not path.exists():
        return
    now = now or datetime.now(JST)
    want = set(themes)
    kept, moved = [], []
    text = read(path)
    head, rest = text.split(UNUSED_HEAD, 1) if UNUSED_HEAD in text else (text, "")
    body, sep, tail = rest.partition("\n## ")
    for ln in body.splitlines():
        t = _theme_line(ln)
        if t and t in want and t not in moved:
            moved.append(t)
        else:
            kept.append(ln)
    tail = ("## " + tail) if sep else f"{USED_HEAD}\n"
    if not tail.startswith(USED_HEAD):
        tail = f"{USED_HEAD}\n\n" + tail
    used_head, _, used_body = tail.partition("\n")
    used_body = "".join(f"- {now:%Y-%m-%d} {t}\n" for t in moved) + used_body.lstrip("\n")
    new = head + UNUSED_HEAD + "\n".join(kept).rstrip("\n") + "\n\n" + used_head + "\n" + used_body
    path.write_text(new, encoding="utf-8")


def write_otehon(threads: list[dict], keep: int = 5, path: Path | None = None) -> str:
    """伸びたスレッドを、前の版と合わせて表示数の多い順に keep 本まで書く。「手で残すお手本」の節はそのまま残す。"""
    path = path or OTEHON
    old = read(path)
    by_url: dict[str, dict] = {}
    auto_part = old.split(KEEP_HEAD, 1)[0]
    for t in parse_otehon(auto_part.split(AUTO_HEAD, 1)[-1]) + threads:
        if t.get("posts"):
            by_url[t.get("url") or t["head"]] = t
    real = [t for t in by_url.values() if SAMPLE_MARK not in t.get("head", "")]
    pool = real if real else list(by_url.values())  # 自分の伸びたスレッドが入ったら、見本は外す
    best = sorted(pool, key=lambda t: -t.get("views", 0))[:keep]
    intro = old.split(AUTO_HEAD, 1)[0].rstrip() if AUTO_HEAD in old else (
        "# お手本\n\n<!-- 自分のアカウントで伸びたスレッドの全文です。投稿を書く AI は、この形・長さ・言い回しをまねします（中身や数字は使いません）。 -->"
    )
    lines = [intro, "", AUTO_HEAD, ""]
    for t in best:
        head = t["head"] if t.get("head") and "表示" in t["head"] else f"{t.get('views', 0):,}表示　{t.get('date', '')}　{t.get('url', '')}".strip()
        lines += [f"### {head}", ""]
        for j, p in enumerate(t["posts"], 1):
            lines += [f"{j}つ目：", p, ""]
    keep_part = old.split(KEEP_HEAD, 1)[1] if KEEP_HEAD in old else "\n\n<!-- 自分で手で書いた投稿で、残したい物があれば、上と同じ形でここに足してください。 -->\n"
    text = "\n".join(lines).rstrip() + "\n\n" + KEEP_HEAD + keep_part.rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    return text


# ---- 言い出し（1つ目の最初の一言）の候補 ----
IIDASHI = MEM / "言い出し.md"
IIDASHI_HEAD = "## 本人が使った言い出し（毎週日曜に自動で足されます）"
_IIDASHI_LINE = re.compile(r"^\s*-\s*「(.+?)」(?:（([\d,]+)表示）)?")


def opener(text: str) -> str:
    """投稿の最初の一言（最初の読点・句点まで）。言い出しが重なっていないかを見るのに使う。"""
    t = re.sub(r"\s+", "", text or "").lstrip("「")
    m = re.match(r"^(.{2,20}?)[、。！？?!…]", t)
    return (m.group(1) if m else t[:12]).rstrip("」")


def _good_opener(s: str) -> bool:
    """候補にしてよい言い出しか。数字入り・長すぎ・返信・締めの言葉は外す。"""
    return 3 <= len(s) <= 15 and not re.search(r"[\d０-９@#]", s) and not s.startswith(("まとめると", "http"))


def iidashi_list(text: str | None = None) -> list[tuple[str, int]]:
    text = strip_comments(read(IIDASHI) if text is None else text)
    out = []
    for ln in text.splitlines():
        m = _IIDASHI_LINE.match(ln)
        if m:
            out.append((m.group(1).replace(SAMPLE_MARK, ""), int((m.group(2) or "0").replace(",", ""))))
    return out


def iidashi_for_prompt() -> str:
    return "\n".join(f"- {s}" + (f"（{v:,}表示）" if v else "") for s, v in iidashi_list())


def write_iidashi(heads: list[dict], keep: int = 40, path: Path | None = None) -> str:
    """スレッドの先頭の言い出しを、表示の多い順に足す（前からある物は残し、表示は大きい方を取る）。"""
    path = path or IIDASHI
    old = read(path)
    best: dict[str, int] = {}
    for s, v in iidashi_list(old):
        best[s] = max(best.get(s, 0), v)
    sample_only = SAMPLE_MARK in old
    new_any = False
    for h in heads:
        t = re.sub(r"\s+", "", h.get("text", "")).lstrip("「")
        if not re.match(r"^.{2,20}?[、。！？?!…]", t):  # 区切りが無い（途中で切れる）物は候補にしない
            continue
        s = opener(t)
        if not _good_opener(s):
            continue
        v = int(h.get("impression_count") or 0)
        if s not in best or v > best[s]:
            new_any = True
        best[s] = max(best.get(s, 0), v)
    if sample_only and new_any:  # 本物が入ったら、見本は外す
        best = {s: v for s, v in best.items() if v > 0}
    rows = sorted(best.items(), key=lambda x: -x[1])[:keep]
    head = old.split(IIDASHI_HEAD)[0].rstrip() if IIDASHI_HEAD in old else (
        "# 言い出し\n\n<!-- 1つ目の投稿の最初の一言の候補です。投稿を書く AI は、ここかお手本の言い出しから選び、直近の投稿と同じ言い出しは使いません。 -->"
    )
    body = "\n".join(f"- 「{s}」" + (f"（{v:,}表示）" if v else "") for s, v in rows)
    text = f"{head}\n\n{IIDASHI_HEAD}\n\n{body}\n"
    path.write_text(text, encoding="utf-8")
    return text
