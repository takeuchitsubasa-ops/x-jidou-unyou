"""毎週日曜の夜：先週の分析 → 伸びる型.md の更新。"""
from __future__ import annotations

import json
import re
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import issues, llm, memory, state, xapi
import difflib
from .config import JST, ROOT

BIG_CHANGE = 0.7  # 前の版との似ている度合いがこれ未満なら、Issue で知らせる
REQUIRED = ["## 先週の動き", "## 文の形（つかみ）", "## 投稿の種類", "## 落ちた型", "## 避けること", "## 文体", "## 実例"]


def hook_phrase(opening: str) -> str:
    """つかみの言葉（最初の読点・句点まで。長い時は切る）。生成時に型が付いていない投稿の集計に使う。"""
    t = re.sub(r"\s+", "", opening)
    m = re.match(r"^(.{2,18}?)[、。！？?!]", t)
    return m.group(1) if m else t[:14]


def own_posts(rows: list[dict], uid: str | None) -> list[dict]:
    """他の人への返信を除く（自分のスレッド内の返信は残す）。"""
    out = []
    for r in rows:
        to = r.get("in_reply_to_user_id")
        if to and uid and to != uid:
            continue
        out.append(r)
    return out


def thread_heads(rows: list[dict], uid: str | None = None) -> list[dict]:
    """投稿を conversation_id でまとめ、スレッドの先頭（つかみ）の数字を返す。他人への返信は集計に入れない。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in own_posts(rows, uid):
        groups[r.get("conversation_id") or r["id"]].append(r)
    heads = []
    for cid, g in groups.items():
        head = next((x for x in g if x["id"] == cid), None) or max(g, key=lambda x: x.get("impression_count") or 0)
        h = dict(head)
        h["thread_len"] = len(g)
        heads.append(h)
    return heads


def top_threads(rows: list[dict], uid: str | None, user_name: str, n: int = 5, min_len: int = 2) -> list[dict]:
    """伸びたスレッドを、全文ごと返す（お手本.md 用）。他人への返信は入れない。表示数はスレッドの先頭の数字。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in own_posts(rows, uid):
        groups[r.get("conversation_id") or r["id"]].append(r)
    out = []
    for cid, g in groups.items():
        g = sorted(g, key=lambda x: x.get("created_at") or "")
        head = next((x for x in g if x["id"] == cid), None)
        if head is None or len(g) < min_len or head.get("impression_count") is None:
            continue
        posts = [re.sub(r"^(?:@\w+\s+)+", "", (x.get("text") or "")).strip() for x in g]
        out.append(
            {
                "views": head["impression_count"],
                "date": (head.get("created_at") or "")[:10],
                "url": f"https://x.com/{user_name}/status/{cid}",
                "posts": [p for p in posts if p],
            }
        )
    return sorted(out, key=lambda t: -t["views"])[:n]


def _stat(v: list[int]) -> dict:
    return {"n": len(v), "median": int(st.median(v)), "max": max(v)}


def summarize(heads: list[dict], posted: list[dict] | None = None) -> dict:
    """生成時に付けた型（hook_type）と種類ごとの表示数を主にする。型が付いていない投稿は、つかみの言葉で数える。"""
    by_id = {}
    for p in posted or []:
        if p.get("tweet_ids") and not p.get("失敗"):
            by_id[p["tweet_ids"][0]] = p
    by_type: dict[str, list[int]] = defaultdict(list)
    by_kind: dict[str, list[int]] = defaultdict(list)
    by_hook: dict[str, list[int]] = defaultdict(list)
    for h in heads:
        v = h.get("impression_count")
        if v is None:
            continue
        p = by_id.get(h["id"])
        if p:
            by_type[p.get("型") or "（型なし）"].append(v)
            by_kind[p.get("種類") or "（種類なし）"].append(v)
        else:
            by_hook[hook_phrase(h["text"])].append(v)
    ranked = sorted([h for h in heads if h.get("impression_count") is not None], key=lambda h: -h["impression_count"])
    vals = [h["impression_count"] for h in ranked]
    return {
        "threads": len(ranked),
        "median_impressions": int(st.median(vals)) if vals else None,
        "by_hook_type": {k: _stat(v) for k, v in by_type.items()},
        "by_kind": {k: _stat(v) for k, v in by_kind.items()},
        "by_opening_phrase": {k: _stat(v) for k, v in by_hook.items() if len(v) >= 2},
        "top": [_brief(h, by_id) for h in ranked[:10]],
        "bottom": [_brief(h, by_id) for h in ranked[-10:]],
    }


def _brief(h: dict, by_id: dict) -> dict:
    p = by_id.get(h["id"]) or {}
    return {"id": h["id"], "views": h.get("impression_count"), "likes": h.get("like_count"), "text": re.sub(r"\s+", " ", h["text"])[:120], "hook_type": p.get("型", "")}


def prompt(user: str, summary: dict, since: datetime) -> tuple[str, str]:
    system = (
        "あなたは、Xの運用の分析担当です。経営者本人のアカウントの数字から、伸びる型を言葉にして、メモの md を書き換えます。\n"
        "決まり：\n"
        "1. 数字（表示数・本数・中央値）は、渡したデータにある物だけを、そのまま使います。計算し直した数字、推測の数字は書きません。\n"
        "2. 実例には、必ず投稿のURLと表示数を付けます。URLは渡したデータの物だけです。\n"
        "3. 「事実（データで言えること）」と「推測（原因の見立て）」を分けて書きます。本数が少ない時は、そう書きます。型ごとの強さは、データの by_hook_type（生成時に付けた型ごとの表示数）を主な根拠にします。型が付いた投稿が少ない間は、by_opening_phrase（つかみの言葉）は参考程度にします。\n"
        "4. 見出しは次の7つを、この名前のまま、この順で残します：" + "、".join(REQUIRED) + "。\n"
        "5. 前の版の内容のうち、今週のデータと矛盾しない物は残し、書き換えるのは、データで変わった所だけです。\n"
        "6. 「文の形（つかみ）」には、伸びた言い出しを、投稿の最初の一言のまま「」で挙げ、表示数とURLを付けます（投稿を書く担当が、その言い出しをそのまま使います）。\n"
        "7. 文体の節は、本人が手で書いた投稿の言い回しを元にします。AI が書いた言い回しは足しません。\n"
        "出力は、md の全文だけです。"
    )
    old = memory.read(memory.KATA)
    body = (
        f"<今の伸びる型.md>\n{old}\n</今の伸びる型.md>\n\n"
        f"<今週のデータ（{since:%Y-%m-%d} 以降）>\n{json.dumps(summary, ensure_ascii=False, indent=1)}\n</今週のデータ>\n\n"
        "伸びる型.md の新しい全文を書いてください。"
    )
    return system, body


def update_otehon(rows: list[dict], uid: str | None, user_name: str, cfg: dict, hist, stamp: str) -> None:
    """お手本.md を、伸びたスレッドの全文で入れ替える（前の版より伸びた物だけが入る）。数字の計算は AI にさせない。"""
    tops = top_threads(rows, uid, user_name, min_len=cfg["投稿"]["スレッドの長さ"])
    if not tops:
        return
    if memory.OTEHON.exists():
        (hist / f"お手本_{stamp}.md").write_text(memory.read(memory.OTEHON), encoding="utf-8")
    memory.write_otehon(tops)
    print("お手本.md を、伸びたスレッドで入れ替えました")
    if memory.IIDASHI.exists():
        (hist / f"言い出し_{stamp}.md").write_text(memory.read(memory.IIDASHI), encoding="utf-8")
    memory.write_iidashi(thread_heads(rows, uid))
    print("言い出し.md に、今週のスレッドの言い出しを足しました")


def validate(new: str, allowed_text: str) -> list[str]:
    """書き換えた md が、決まりを守っているか。"""
    problems = [f"見出しがありません：{h}" for h in REQUIRED if h not in new]
    pat = r"https?://[^\s)）」\"',]+"
    allowed_urls = set(re.findall(pat, allowed_text))
    for u in set(re.findall(pat, new)):
        if u not in allowed_urls:
            problems.append(f"データに無いURL：{u}")
    allowed_nums = set(re.findall(r"\d[\d,]*", allowed_text.replace("，", ",")))
    for m in re.finditer(r"(\d[\d,]*)\s*(?:表示|回表示)", new):
        if m.group(1) not in allowed_nums:
            problems.append(f"データに無い表示数：{m.group(1)}")
    return problems


def run(cfg: dict, *, dry: bool = False, no_x: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now(JST)
    since = now - timedelta(days=cfg["分析"]["日数"])
    uid = None
    rows: list[dict] = []
    if no_x or not xapi.have_keys():
        # X を読まない時は、毎朝ためた数字（data/数字.jsonl）だけで分析する
        from .morning import latest_metrics

        rows = [r for r in latest_metrics() if (r.get("created_at") or "9999") >= since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")]
        uid = state.user_id() if state.ACCOUNT.exists() else None
        if not rows:
            print("分析する数字がありません（--no-x か X の鍵が無く、data/数字.jsonl も空です）")
            return 0
    else:
        c = xapi.XClient()
        uid = state.user_id(c) if state.ACCOUNT.exists() or not dry else c.me()["id"]
        rows_raw = c.my_posts(uid, since.astimezone(timezone.utc), cfg["分析"]["最大件数"])
        rows = [
            {
                "id": r["id"], "created_at": r.get("created_at"), "text": r.get("text", ""), "conversation_id": r.get("conversation_id"),
                "in_reply_to_user_id": r.get("in_reply_to_user_id"),
                "impression_count": r.get("public_metrics", {}).get("impression_count"), "like_count": r.get("public_metrics", {}).get("like_count"),
                "reply_count": r.get("public_metrics", {}).get("reply_count"), "retweet_count": r.get("public_metrics", {}).get("retweet_count"),
                "bookmark_count": r.get("public_metrics", {}).get("bookmark_count"), "quote_count": r.get("public_metrics", {}).get("quote_count"),
            }
            for r in rows_raw
        ]
        if not dry:
            state.append_jsonl(state.METRICS, [{**r, "取得": now.isoformat(timespec="seconds")} for r in rows])
    heads = thread_heads(rows, uid)
    summary = summarize(heads, state.read_jsonl(state.POSTED))
    user_name = cfg["アカウント名"] or "i"
    for lst in (summary["top"], summary["bottom"]):
        for b in lst:
            b["url"] = f"https://x.com/{user_name}/status/{b['id']}"
    system, body = prompt("", summary, since)
    new = llm.text(cfg, system=system, user=body, model=cfg["AI"]["分析のモデル"], max_tokens=8000).strip()
    new = re.sub(r"^```(?:markdown|md)?\n|\n```$", "", new)
    old = memory.read(memory.KATA)
    allowed = old + json.dumps(summary, ensure_ascii=False)
    problems = validate(new, allowed)
    out = ROOT / "出力"
    out.mkdir(exist_ok=True)
    stamp = f"{now:%Y%m%d}"
    if problems:
        (out / f"伸びる型_案_{stamp}.md").write_text(new + "\n\n<!-- 見送った理由：" + "；".join(problems) + " -->\n", encoding="utf-8")
        print("伸びる型の更新を見送りました：" + "；".join(problems))
        if not dry:  # お手本は数字から機械的に作るので、伸びる型を見送っても入れ替える
            (memory.MEM / "履歴").mkdir(parents=True, exist_ok=True)
            update_otehon(rows, uid, user_name, cfg, memory.MEM / "履歴", stamp)
        if not dry and issues.available():
            gh = issues.Issues()
            gh.ensure_all(cfg["確認"]["OKラベル"])
            gh.create("伸びる型の更新を見送りました", "AI が書いた案が決まりを守っていなかったので、前の版のままにしています。\n\n" + "\n".join(f"- {p}" for p in problems), [issues.LABEL_NOTICE])
        return 0
    if dry:
        (out / f"伸びる型_案_{stamp}.md").write_text(new + "\n", encoding="utf-8")
        tops = top_threads(rows, uid, user_name, min_len=cfg["投稿"]["スレッドの長さ"])
        if tops:
            tmp = out / f"お手本_案_{stamp}.md"
            tmp.write_text(memory.read(memory.OTEHON), encoding="utf-8")
            memory.write_otehon(tops, path=tmp)
        print("試し運転：伸びる型の新しい案を 出力/ に書きました（メモリは変えていません）")
        return 0
    hist = memory.MEM / "履歴"
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"伸びる型_{stamp}.md").write_text(old, encoding="utf-8")
    update_otehon(rows, uid, user_name, cfg, hist, stamp)
    memory.KATA.write_text(new + "\n", encoding="utf-8")
    wk = ROOT / "data" / "週次"
    wk.mkdir(parents=True, exist_ok=True)
    (wk / f"{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("伸びる型.md を更新しました")
    ratio = difflib.SequenceMatcher(None, old, new).ratio() if old.strip() else 1.0
    if ratio < BIG_CHANGE and issues.available():
        gh = issues.Issues()
        gh.ensure_all(cfg["確認"]["OKラベル"])
        gh.create(
            "伸びる型が大きく変わりました",
            f"今週の分析で、伸びる型.md の中身が、前の版から大きく変わりました（似ている度合い {ratio:.0%}）。\n\n"
            f"前の版は `メモリ/履歴/伸びる型_{stamp}.md` に残してあります。新しい版を読んで、おかしければ、前の版に戻してください。",
            [issues.LABEL_NOTICE],
        )
    return 0
