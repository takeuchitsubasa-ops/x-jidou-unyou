"""朝の準備：ネタを取り込む → 数字を取る → 案を作る → 確かめる → 予約の列に入れる。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import generate, issues, llm, memory, state, xapi
from .config import JST, ROOT
from .verify import extract_numbers

LONG = 1200


def condense(cfg: dict, body: str) -> str:
    """長い文字起こしは、事実だけの箇条書きにする。数字は元の文に有る物だけ。"""
    if len(body) <= LONG:
        return body
    try:
        out = llm.text(
            cfg,
            system="次の文章から、事実だけを箇条書きにします。数字・金額・固有名は一字も変えません。推測や言い換えや感想を足しません。話し言葉の重複は1つにまとめます。",
            user=body,
            model=cfg["AI"]["確認用のモデル"],
            max_tokens=3000,
        )
        orig = {(n.value, n.unit) for n in extract_numbers(body)}
        if all((n.value, n.unit) in orig for n in extract_numbers(out)):
            return out.strip() + "\n\n（長い文章だったので、AI が事実だけに縮めました。元の文は Issue に残っています）"
    except Exception as e:  # noqa: BLE001
        print(f"  ネタの要約に失敗：{e}（元の文を使います）")
    return body[:4000]


def intake_neta(cfg: dict, gh: issues.Issues | None, dry: bool, now: datetime) -> list[str]:
    log: list[str] = []
    if gh is None:
        return ["GitHub に接続していないので、ネタの取り込みは飛ばしました"]
    for it in gh.list(issues.LABEL_NETA):
        title, body = it["title"], it.get("body") or ""
        log.append(f"ネタ #{it['number']}：{title}")
        if dry:
            continue
        text = condense(cfg, body)
        memory.append_neta(title, text, f"Issue #{it['number']}（本人の入力）", now)
        gh.close(it["number"], "ネタ帳に入れました。次の実行から、このネタを使った投稿が作られます。")
    if not log:
        log.append("新しいネタはありませんでした")
    return log


def intake_theme(gh: issues.Issues | None, dry: bool) -> list[str]:
    """「テーマ」ラベルの Issue を、テーマ.md に入れる。題がテーマ、本文は補足（ある時だけ）。"""
    log: list[str] = []
    if gh is None:
        return log
    for it in gh.list(issues.LABEL_THEME):
        title, body = it["title"].strip(), re.sub(r"\s+", " ", (it.get("body") or "")).strip()
        theme = f"{title}：{body}" if body else title
        log.append(f"テーマ #{it['number']}：{theme[:40]}")
        if dry:
            continue
        memory.add_theme(theme)
        gh.close(it["number"], "テーマに入れました。次の朝の準備で、このテーマのスレッドが作られます。")
    return log


def fetch_metrics(cfg: dict, dry: bool, now: datetime) -> tuple[list[dict], str]:
    if not xapi.have_keys():
        return [], "X の鍵が無いので、数字は取りませんでした"
    c = xapi.XClient()
    uid = state.user_id(c) if state.ACCOUNT.exists() or not dry else c.me()["id"]
    since = xapi.since_days(cfg["数字を取る"]["日数"], now.astimezone(timezone.utc))
    rows = c.my_posts(uid, since, cfg["数字を取る"]["最大件数"])
    out = []
    for r in rows:
        pm = r.get("public_metrics", {})
        out.append(
            {
                "取得": now.isoformat(timespec="seconds"),
                "id": r["id"],
                "created_at": r.get("created_at"),
                "text": r.get("text", ""),
                "conversation_id": r.get("conversation_id"),
                "in_reply_to_user_id": r.get("in_reply_to_user_id"),
                "impression_count": pm.get("impression_count"),
                "like_count": pm.get("like_count"),
                "reply_count": pm.get("reply_count"),
                "retweet_count": pm.get("retweet_count"),
                "quote_count": pm.get("quote_count"),
                "bookmark_count": pm.get("bookmark_count"),
            }
        )
    if not dry and out:
        state.append_jsonl(state.METRICS, out)
    return out, f"自分の投稿 {len(out)} 件の数字を取りました（X API の読み取り {c.posts_read} 件）"


def latest_metrics() -> list[dict]:
    by_id: dict[str, dict] = {}
    for r in state.read_jsonl(state.METRICS):
        by_id[r["id"]] = r
    return sorted(by_id.values(), key=lambda r: r.get("created_at") or "")


def run(cfg: dict, *, dry: bool = False, no_x: bool = False, no_github: bool = False, now: datetime | None = None) -> str:
    now = now or datetime.now(JST)
    notes: list[str] = []
    gh = issues.Issues() if (issues.available() and not no_github) else None
    if not dry and cfg["AI"]["提供元"] != "file" and memory.is_sample():
        raise RuntimeError("メモリ（ネタ帳・会社の事実）が、見本のままです。【見本】の行を全部消して、自分の内容に書き換えてから、本番にしてください。")
    if gh and not dry:
        gh.ensure_all(cfg["確認"]["OKラベル"])  # ラベルを最初に全部作る

    notes += intake_neta(cfg, gh, dry, now)
    notes += intake_theme(gh, dry)

    fetched: list[dict] = []
    if no_x:
        notes.append("--no-x のため、X の数字は取りませんでした")
    else:
        try:
            fetched, msg = fetch_metrics(cfg, dry, now)
            notes.append(msg)
        except xapi.XError as e:
            notes.append(f"数字を取れませんでした（続けます）：{e}")

    rows = latest_metrics()
    seen = {r["id"] for r in rows}
    rows += [r for r in fetched if r["id"] not in seen]
    posted = state.read_jsonl(state.POSTED)
    recent = generate.recent_openers([{"text": x["posts"][0]} for x in posted if x.get("posts")] or rows)
    hot = generate.hot_posts(rows)

    plan_day = generate.plan_date(now, cfg)
    q = state.load_queue()
    for it in q["items"]:
        if it["状態"] == "予約" and it.get("date", "9999") < now.date().isoformat():
            it["状態"] = "期限切れ"
    times = sorted(cfg["投稿"]["時刻"])
    ready = [it for it in q["items"] if it.get("date") == plan_day and it["状態"] in ("予約", "確認済み", "一部投稿済み")]
    used = {p["枠"] for p in posted}
    tol = timedelta(minutes=cfg["投稿"]["遅れの許容_分"])

    def _open(t: str) -> bool:
        h, m = map(int, t.split(":"))
        start = datetime.fromisoformat(plan_day).replace(hour=h, minute=m, tzinfo=JST)
        return f"{plan_day} {t}" not in used and now <= start + tol  # もう投稿済み・もう間に合わない枠は埋めない

    free = [t for t in times if t not in {it.get("slot") for it in ready} and _open(t)]
    need = len(free)
    if need <= 0:
        notes.append(f"{plan_day} の分は、もう予約の列に入っています")
        text = generate.render_output(plan_day, [], cfg, "\n".join(f"- {n}" for n in notes))
        _write_output(plan_day, text)
        return text

    n_cand = need if cfg["確認"]["方式"] == "全部確認する" else need + cfg["確認"]["予備の案の数"]
    themes = memory.unused_themes()[:need]
    notes.append("今日のテーマ：" + ("／".join(themes) if themes else "（テーマが無いので、材料から選びます）"))
    used = generate.used_quotes(q["items"], plan_day)
    cands = generate.make_candidates(cfg, n_cand, recent, hot, themes, used)
    items = generate.judge(cfg, cands, need, recent, used)

    slot_iter = iter(free)
    for it in items:
        it["date"] = plan_day
        if it["状態"] == "予約":
            it["slot"] = next(slot_iter)
    if gh and not dry:
        for it in items:
            if it["状態"] == "確認待ち":
                it["issue"] = gh.create(f"確認待ち：{it['題']}", generate.hold_issue_body(it, cfg), [issues.LABEL_HOLD])
    kept = [it for it in items if it["状態"] in ("予約", "確認待ち")]
    if not dry:
        q["items"].extend(kept)
        # 古い物は、増え続けないよう100件まで残す
        q["items"] = q["items"][-100:]
        state.save_queue(q)
        memory.mark_themes_used(sorted({it["テーマ"] for it in kept if it.get("テーマ") in themes}), now)
    notes.append(f"案 {len(items)} 本 → 予約 {sum(i['状態']=='予約' for i in items)}・確認待ち {sum(i['状態']=='確認待ち' for i in items)}・捨てた {sum(i['状態']=='捨てた' for i in items)}")
    text = generate.render_output(plan_day, items, cfg, "\n".join(f"- {n}" for n in notes))
    _write_output(plan_day, text)
    return text


def _write_output(plan_day: str, text: str) -> None:
    d = ROOT / "出力"
    d.mkdir(exist_ok=True)
    (d / f"{plan_day}_投稿の案.md").write_text(text, encoding="utf-8")
