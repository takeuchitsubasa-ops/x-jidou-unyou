"""投稿：予約の列から1本取って、スレッドで投稿する。

30分ごとに動き、「今の枠（設定.yml の時刻）がまだ空いていて、遅れが許容の範囲内」の時だけ投稿する。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import issues, state, xapi
from .config import JST, ROOT

MAX_TRIES = 3  # 途中まで出た物を、続きから出し直す回数


def due_slot(now: datetime, times: list[str], used: set[str], tolerance_min: int) -> str | None:
    """今投稿する枠（"HH:MM"）。無ければ None。一番早い、未使用で、遅れが許容内の枠。"""
    for t in sorted(times):
        h, m = map(int, t.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= now <= start + timedelta(minutes=tolerance_min) and f"{now.date()} {t}" not in used:
            return t
    return None


def refresh_holds(cfg: dict, q: dict, gh: issues.Issues | None) -> None:
    """確認待ちの Issue に「OK」が付いたか見る。閉じられていたら出さない。"""
    if gh is None:
        return
    ok = cfg["確認"]["OKラベル"]
    for it in q["items"]:
        if it["状態"] != "確認待ち" or not it.get("issue"):
            continue
        labels = gh.labels_of(it["issue"])
        if ok in labels:
            it["状態"] = "確認済み"
        else:
            r = gh.s.get(gh._url(f"/issues/{it['issue']}"), timeout=30).json()
            if r.get("state") == "closed":
                it["状態"] = "捨てた"
                it["捨てた理由"] = "Issue が「OK」なしで閉じられました"


def pick(q: dict, today: str) -> dict | None:
    """途中まで出た物を先に、次に確認済み、次に今日の予約。"""
    for status in ("一部投稿済み", "確認済み", "予約"):
        cands = [it for it in q["items"] if it["状態"] == status and (status != "予約" or it.get("date") == today)]
        if cands:
            return sorted(cands, key=lambda it: (it.get("date", ""), it.get("slot", "")))[0]
    return None


def run(cfg: dict, *, dry: bool = False, no_github: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now(JST)
    posted = state.read_jsonl(state.POSTED)
    used = {p["枠"] for p in posted}
    slot = due_slot(now, cfg["投稿"]["時刻"], used, cfg["投稿"]["遅れの許容_分"])
    if slot is None:
        print("今は投稿の枠ではありません（または、この枠は投稿済みです）")
        return 0
    q = state.load_queue()
    gh = issues.Issues() if (issues.available() and not no_github) else None
    refresh_holds(cfg, q, gh)
    item = pick(q, now.date().isoformat())
    if item is None:
        print(f"{slot} の枠に出す物がありません（朝の準備が動いていないか、全部確認待ちです）")
        if not dry:
            state.save_queue(q)
        return 0
    if dry:
        text = f"# {now.date()} {slot} の枠で出す予定の投稿（試し運転・X には投稿していません）\n\n" + f"題：{item['題']}\n\n" + "\n\n---\n\n".join(item["posts"]) + "\n"
        d = ROOT / "出力"
        d.mkdir(exist_ok=True)
        (d / f"{now:%Y-%m-%d_%H%M}_投稿予定.md").write_text(text, encoding="utf-8")
        print(text)
        return 0

    client = xapi.XClient()
    ids: list[str] = item.get("tweet_ids") or []
    try:
        for i, text in enumerate(item["posts"]):
            if i < len(ids):  # 前回、途中まで投稿できていた時は、続きから
                continue
            ids.append(client.post(text, reply_to=ids[-1] if ids else None))
            item["tweet_ids"] = ids
    except Exception as e:  # noqa: BLE001
        item["失敗の理由"] = str(e)[:300]
        item["試行"] = item.get("試行", 0) + 1
        final = not ids or item["試行"] >= MAX_TRIES
        # 1つも出ていない失敗は、その枠をその日は止める（同じ枠で何度も試して、二重に出さないため）。
        # 途中まで出た物は「一部投稿済み」にして、次の実行で続きから出す（MAX_TRIES 回まで）。
        item["状態"] = "失敗" if final else "一部投稿済み"
        state.save_queue(q)
        if final:
            state.append_jsonl(state.POSTED, [_record(now, slot, item, ids, failed=True)])
        if gh and (final or item["試行"] == 1):
            gh.ensure_label(issues.LABEL_FAIL, "d73a4a", "投稿に失敗した")
            tail = "この枠は、今日はもう試しません。" if final else "次の実行で、続きから出します。"
            gh.create(f"投稿に失敗：{item['題']}", f"{slot} の枠の投稿に失敗しました。{tail}\n\n理由：{e}\n\n途中まで出た投稿のID：{ids}\n\nX の画面で、出ている所を確かめてください。", [issues.LABEL_FAIL])
        print(f"失敗：{e}")
        return 1
    item["状態"] = "投稿済み"
    state.save_queue(q)
    state.append_jsonl(state.POSTED, [_record(now, slot, item, ids)])
    print(f"{slot} の枠で投稿しました：{item['題']}（{len(ids)}投稿）")
    return 0


def _record(now: datetime, slot: str, item: dict, ids: list[str], failed: bool = False) -> dict:
    return {
        "失敗": failed,
        "投稿日時": now.isoformat(timespec="seconds"),
        "枠": f"{now.date()} {slot}",
        "id": item["id"],
        "tweet_ids": ids,
        "題": item["題"],
        "型": item["型"],
        "種類": item["種類"],
        "ネタ元": item["ネタ元"],
        "posts": item["posts"][: len(ids)],
    }
