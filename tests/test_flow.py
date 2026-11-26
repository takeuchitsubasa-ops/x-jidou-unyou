import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xjido import analyze, config, generate, memory  # noqa: E402
from xjido.config import JST  # noqa: E402
from xjido.publish import due_slot, pick  # noqa: E402

MEMORY = "## 料金\n- 訪問料金は中央市なら2,000円から。\n"


def _cand(text_num: str, quote: str):
    return {
        "title": "t", "hook_type": "x", "kind": "k", "source_note": "n",
        "posts": [f"料金の話です（{text_num[:4]}）。", text_num, "気になる時は相談してください。"],  # 言い出しが重ならないよう、案ごとに変える
        "sources": [{"expression": "e", "origin": "会社の事実", "quote": quote}] if quote else [],
    }


def _setup(monkeypatch, mode):
    cfg = config.load()
    cfg["確認"]["方式"] = mode
    cfg["確認"]["AIで二重に確かめる"] = False
    monkeypatch.setattr(memory, "corpus_text", lambda: MEMORY)
    return cfg


def test_default_mode_holds_unsupported_and_reserves_supported(monkeypatch):
    cfg = _setup(monkeypatch, "止まった時だけ確認")
    good = _cand("中央市なら2,000円からです。", "訪問料金は中央市なら2,000円から。")
    bad = _cand("月5,000円下がる家があります。", "")
    items = generate.judge(cfg, [bad, good], need=1)
    assert [i["状態"] for i in items] == ["確認待ち", "予約"]
    assert "5000円" in items[0]["確認の理由"]


def test_all_mode_holds_everything(monkeypatch):
    cfg = _setup(monkeypatch, "全部確認する")
    good = _cand("中央市なら2,000円からです。", "訪問料金は中央市なら2,000円から。")
    items = generate.judge(cfg, [good], need=1)
    assert items[0]["状態"] == "確認待ち"


def test_off_mode_drops_unsupported_without_hold(monkeypatch):
    cfg = _setup(monkeypatch, "止まった案は捨てる")
    bad = _cand("月5,000円下がる家があります。", "")
    items = generate.judge(cfg, [bad], need=1)
    assert items[0]["状態"] == "捨てた"


def test_matome_limit(monkeypatch):
    cfg = _setup(monkeypatch, "止まった時だけ確認")
    a, b = _cand("二つ目。", ""), _cand("二つ目です。", "")
    a["posts"][-1] = "まとめると、一つ目。"
    b["posts"][-1] = "まとめると、二つ目。"
    items = generate.judge(cfg, [a, b], need=2)
    assert [i["状態"] for i in items] == ["予約", "捨てた"]


def test_due_slot():
    t = lambda h, m: datetime(2026, 11, 27, h, m, tzinfo=JST)  # noqa: E731
    times = ["06:00", "12:00", "18:00"]
    assert due_slot(t(6, 5), times, set(), 180) == "06:00"
    assert due_slot(t(6, 5), times, {"2026-11-27 06:00"}, 180) is None
    assert due_slot(t(9, 30), times, set(), 180) is None  # 遅れすぎ
    assert due_slot(t(5, 59), times, set(), 180) is None
    assert due_slot(t(12, 30), times, {"2026-11-27 06:00"}, 180) == "12:00"


def test_pick_prefers_confirmed_then_today():
    q = {"items": [
        {"id": "a", "状態": "予約", "date": "2026-11-27", "slot": "06:00"},
        {"id": "b", "状態": "確認済み", "date": "2026-11-26", "slot": ""},
        {"id": "c", "状態": "予約", "date": "2026-11-26", "slot": "06:00"},
    ]}
    assert pick(q, "2026-11-27")["id"] == "b"
    q["items"][1]["状態"] = "投稿済み"
    assert pick(q, "2026-11-27")["id"] == "a"


def test_validate_kata_rejects_invented_numbers_and_urls():
    old = "## 実例\n- https://x.com/a/status/1 は 1,315,975 表示\n"
    allowed = old + '{"views": 4821}'
    good = "".join(f"{h}\n" for h in analyze.REQUIRED) + "- https://x.com/a/status/1 は 1,315,975 表示\n"
    assert analyze.validate(good, allowed) == []
    bad = good + "- https://x.com/a/status/2 は 999,999 表示\n"
    probs = analyze.validate(bad, allowed)
    assert any("URL" in p for p in probs) and any("表示数" in p for p in probs)
    assert analyze.validate("## 先週の動き\n", allowed)  # 見出し不足
