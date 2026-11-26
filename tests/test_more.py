import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xjido import analyze, config, generate, llm, memory, publish  # noqa: E402
from xjido.config import JST  # noqa: E402
from xjido.verify import Corpus, check_thread, extract_numbers  # noqa: E402

CFG = config.load()
MEM = """
## 2026-09-03 水抜き栓の話
- 出どころ：Issue #12
給湯器の水抜き栓が固着していた。
## 数字
- 訪問は中央市なら2,000円から。 要本人確認
- 資格は42件。
"""
C = Corpus(MEM)
SRC42 = [{"expression": "e", "origin": "会社の事実", "quote": "資格は42件。"}]


def t(*a):
    return list(a)


def test_default_config_values():
    assert CFG["投稿"]["1投稿の最大文字数"] == 280
    assert CFG["AI"]["確認用のモデル"] == "claude-sonnet-5-5"
    assert CFG["確認"]["AIで二重に確かめる"] is True
    assert CFG["確認"]["方式"] == "止まった時だけ確認"


def test_old_mode_names_still_work(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text("確認:\n  方式: しない\n", encoding="utf-8")
    assert config.load(p)["確認"]["方式"] == "止まった案は捨てる"


@pytest.mark.parametrize("w", ["最近", "この前", "この間", "以前", "昨日", "昨夜", "お客さまから", "ご依頼", "と言われました"])
def test_more_story_words_need_neta_source(w):
    posts = t(f"{w}、こういうことがありました。", "二つ目。", "三つ目。")
    r = check_thread(posts, [], C, CFG)
    assert not r.ok and any("ネタ帳" in x for x in r.reasons)


@pytest.mark.parametrize("w", ["半年", "半分", "ほとんど", "全員", "必ず", "倍"])
def test_quantity_words_need_source(w):
    posts = t(f"{w}の家で起きます。", "二つ目。", "三つ目。")
    r = check_thread(posts, [], C, CFG)
    assert not r.ok and any("言い方" in x for x in r.reasons)


def test_quantity_word_passes_when_quote_has_it():
    mem = Corpus("## 話\n- 工事の前には必ず電源を切る。\n")
    src = [{"expression": "必ず", "origin": "会社の事実", "quote": "工事の前には必ず電源を切る"}]
    assert check_thread(t("工事の前には必ず電源を切ります。", "二つ目。", "三つ目。"), src, mem, CFG).ok


def test_new_units():
    assert [n.label() for n in extract_numbers("3億円、5箇所、2ヶ所")][1:] == ["5箇所", "2箇所"]
    assert [n.label() for n in extract_numbers("2兆円")] == ["2兆"]
    assert not check_thread(t("5箇所で起きます。", "二つ目。", "三つ目。"), [], C, CFG).ok


def test_neta_heading_date_is_not_material(monkeypatch, tmp_path):
    f = tmp_path / "ネタ帳.md"
    f.write_text("## 2026-09-03 水抜き栓\n- 出どころ：Issue #12\n固着していた。\n", encoding="utf-8")
    monkeypatch.setattr(memory, "NETA", f)
    monkeypatch.setattr(memory, "FACTS", tmp_path / "none.md")
    c = Corpus(memory.corpus_text())
    assert not any(v in c.values for v in ("2026", "09", "03", "12"))


def test_unconfirmed_lines_are_not_material():
    assert not check_thread(t("訪問は2,000円からです。", "二つ目。", "三つ目。"),
                            [{"expression": "e", "origin": "会社の事実", "quote": "訪問は中央市なら2,000円から。"}], C, CFG).ok
    assert check_thread(t("資格は42件です。", "二つ目。", "三つ目。"), SRC42, C, CFG).ok


def test_plan_date():
    cfg = config.load()
    d = lambda h, m: datetime(2026, 11, 27, h, m, tzinfo=JST)  # noqa: E731
    assert generate.plan_date(d(4, 0), cfg) == "2026-11-27"
    assert generate.plan_date(d(13, 0), cfg) == "2026-11-27"  # 最後の枠（18:00）を過ぎていない
    assert generate.plan_date(d(19, 0), cfg) == "2026-11-28"


def test_pick_continues_partial_first():
    q = {"items": [
        {"id": "a", "状態": "予約", "date": "2026-11-27", "slot": "06:00"},
        {"id": "p", "状態": "一部投稿済み", "date": "2026-11-26", "slot": "18:00"},
    ]}
    assert publish.pick(q, "2026-11-27")["id"] == "p"


class FakeX:
    def __init__(self, fail_at):
        self.fail_at, self.n = fail_at, 0

    def post(self, text, reply_to=None):
        self.n += 1
        if self.n == self.fail_at:
            raise RuntimeError("boom")
        return f"id{self.n}"


def _run_publish(monkeypatch, tmp_path, fail_at, item):
    from xjido import state
    monkeypatch.setattr(state, "QUEUE", tmp_path / "q.json")
    monkeypatch.setattr(state, "POSTED", tmp_path / "p.jsonl")
    state.save_queue({"items": [item]}, tmp_path / "q.json")
    monkeypatch.setattr(publish.xapi, "XClient", lambda: FakeX(fail_at))
    monkeypatch.setattr(state, "load_queue", lambda path=tmp_path / "q.json": json.loads(path.read_text(encoding="utf-8")))
    monkeypatch.setattr(state, "save_queue", lambda q, path=tmp_path / "q.json": path.write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8"))
    cfg = config.load()
    rc = publish.run(cfg, no_github=True, now=datetime(2026, 11, 27, 6, 5, tzinfo=JST))
    return rc, json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))["items"][0], state.read_jsonl(tmp_path / "p.jsonl")


ITEM = {"id": "x", "題": "t", "型": "k", "種類": "s", "ネタ元": "n", "状態": "予約", "date": "2026-11-27", "slot": "06:00", "posts": ["a", "b", "c"], "tweet_ids": []}


def test_total_failure_stops_the_slot_for_the_day(monkeypatch, tmp_path):
    rc, item, posted = _run_publish(monkeypatch, tmp_path, 1, dict(ITEM))
    assert rc == 1 and item["状態"] == "失敗"
    assert posted and posted[0]["失敗"] and posted[0]["枠"] == "2026-11-27 06:00"  # この枠は使用済みになり、再試行しない


def test_partial_failure_resumes_from_where_it_stopped(monkeypatch, tmp_path):
    rc, item, posted = _run_publish(monkeypatch, tmp_path, 2, dict(ITEM))
    assert rc == 1 and item["状態"] == "一部投稿済み" and item["tweet_ids"] == ["id1"]
    assert posted == []  # 枠はまだ使用済みでない → 次の実行で続きから


def test_file_provider_writes_request_then_reads_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("XJIDO_AI_DIR", str(tmp_path))
    cfg = config.load()
    cfg["AI"]["提供元"] = "file"
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    with pytest.raises(llm.LLMWait) as e:
        llm.structured(cfg, system="s", user="u", tool_name="tool", description="d", schema=schema)
    rep = Path(e.value.paths[0])
    req = next(tmp_path.glob("依頼_tool_*.md"))
    assert str(rep) in req.read_text(encoding="utf-8") and '"properties"' in req.read_text(encoding="utf-8")
    rep.write_text('{"a": "b"}', encoding="utf-8")
    assert llm.structured(cfg, system="s", user="u", tool_name="tool", description="d", schema=schema) == {"a": "b"}


def test_sample_memory_is_detected(monkeypatch, tmp_path):
    f = tmp_path / "a.md"
    f.write_text("## 【見本】会社\n", encoding="utf-8")
    monkeypatch.setattr(memory, "NETA", f)
    monkeypatch.setattr(memory, "FACTS", tmp_path / "none.md")
    monkeypatch.setattr(memory, "THEME", tmp_path / "none2.md")
    assert memory.is_sample()
    f.write_text("<!-- 【見本】の説明 -->\n## 会社\n", encoding="utf-8")
    assert not memory.is_sample()


def test_analysis_excludes_replies_to_others_and_uses_hook_type():
    me = "100"
    rows = [
        {"id": "1", "conversation_id": "1", "text": "つかみ。", "impression_count": 5000},
        {"id": "2", "conversation_id": "1", "text": "続き", "impression_count": 900, "in_reply_to_user_id": me},
        {"id": "3", "conversation_id": "3", "text": "他の人への返事", "impression_count": 70, "in_reply_to_user_id": "999"},
    ]
    heads = analyze.thread_heads(rows, me)
    assert [h["id"] for h in heads] == ["1"] and heads[0]["thread_len"] == 2
    s = analyze.summarize(heads, [{"tweet_ids": ["1"], "型": "気づき型", "種類": "Tips"}])
    assert s["by_hook_type"]["気づき型"]["median"] == 5000


# ---- お手本とテーマ ----

def test_theme_add_list_and_mark_used(tmp_path):
    p = tmp_path / "テーマ.md"
    p.write_text("# テーマ\n\n<!-- - コメントの行は使わない -->\n\n## まだ使っていないテーマ\n\n- 【見本】Aの話\n\n## 使ったテーマ\n", encoding="utf-8")
    memory.add_theme("Bの話：先週あった", path=p)
    assert memory.unused_themes(p.read_text(encoding="utf-8")) == ["Aの話", "Bの話：先週あった"]
    memory.mark_themes_used(["Aの話"], datetime(2026, 11, 27, tzinfo=JST), path=p)
    text = p.read_text(encoding="utf-8")
    assert memory.unused_themes(text) == ["Bの話：先週あった"]
    assert "- 2026-11-27 Aの話" in text.split("## 使ったテーマ")[1]


def test_theme_is_material_without_dates(monkeypatch, tmp_path):
    p = tmp_path / "テーマ.md"
    p.write_text("## まだ使っていないテーマ\n- 室外機：雪で埋まっていた家が3軒あった\n## 使ったテーマ\n- 2026-11-20 前の話\n", encoding="utf-8")
    monkeypatch.setattr(memory, "THEME", p)
    c = Corpus(memory.theme_material())
    src = [{"expression": "e", "origin": "テーマ", "quote": "雪で埋まっていた家が3軒あった"}]
    assert check_thread(t("先日、雪で埋まっていた家が3軒あった。", "b", "c"), src, c, CFG).ok
    assert not check_thread(t("2026年の話。", "b", "c"), [], c, CFG).ok


def test_company_practice_needs_source():
    assert not check_thread(t("うちでは必ず見ています。", "b", "c"), [], C, CFG).ok
    assert check_thread(t("うちは資格は42件。", "b", "c"), SRC42, C, CFG).ok


OTEHON = """# お手本

## 伸びたスレッド（毎週日曜に自動で入れ替わります）

### 【見本】0表示　架空

1つ目：
見本の1

2つ目：
見本の2

## 手で残すお手本（自動では変わりません）

### 50表示　2026-10-01　https://x.com/a/status/9

1つ目：
残す1
"""


def test_write_otehon_ranks_drops_samples_and_keeps_manual(tmp_path):
    p = tmp_path / "お手本.md"
    p.write_text(OTEHON, encoding="utf-8")
    assert [len(x["posts"]) for x in memory.parse_otehon(OTEHON)] == [2, 1]
    new = [
        {"views": 100, "date": "2026-11-01", "url": "https://x.com/a/status/1", "posts": ["一", "二", "三"]},
        {"views": 900, "date": "2026-11-02", "url": "https://x.com/a/status/2", "posts": ["い", "ろ", "は"]},
    ]
    text = memory.write_otehon(new, keep=5, path=p)
    ts = memory.parse_otehon(text)
    assert [x["views"] for x in ts] == [900, 100, 50]  # 見本は外れ、多い順、手で残す物はそのまま
    assert "見本の1" not in text and "残す1" in text
    text2 = memory.write_otehon([{"views": 300, "date": "", "url": "https://x.com/a/status/3", "posts": ["x", "y"]}], keep=2, path=p)
    assert [x["views"] for x in memory.parse_otehon(text2.split("## 手で残す")[0])] == [900, 300]


def test_top_threads_full_text_in_order():
    rows = [
        {"id": "1", "conversation_id": "1", "created_at": "2026-11-01T00:00:00Z", "text": "つかみ", "impression_count": 500},
        {"id": "3", "conversation_id": "1", "created_at": "2026-11-01T00:00:02Z", "text": "@me まとめ", "in_reply_to_user_id": "u", "impression_count": 5},
        {"id": "2", "conversation_id": "1", "created_at": "2026-11-01T00:00:01Z", "text": "本文", "in_reply_to_user_id": "u", "impression_count": 9},
        {"id": "7", "conversation_id": "7", "created_at": "2026-11-02T00:00:00Z", "text": "1投稿だけ", "impression_count": 9999},
        {"id": "8", "conversation_id": "x", "created_at": "2026-11-02T00:00:00Z", "text": "@other 返信", "in_reply_to_user_id": "o", "impression_count": 1},
    ]
    tops = analyze.top_threads(rows, "u", "me", min_len=3)
    assert len(tops) == 1 and tops[0]["posts"] == ["つかみ", "本文", "まとめ"] and tops[0]["views"] == 500
    assert tops[0]["url"] == "https://x.com/me/status/1"


def test_prompt_has_otehon_and_themes(monkeypatch, tmp_path):
    p = tmp_path / "お手本.md"
    p.write_text(OTEHON, encoding="utf-8")
    monkeypatch.setattr(memory, "OTEHON", p)
    u = generate.user_prompt(3, [], [], ["凍結の話"])
    assert "見本の1" in u and "1. 凍結の話" in u
    s = generate.system_prompt(CFG)
    assert "お手本" in s and "テーマ" in s
    avg, lo, hi = generate.length_range(CFG)
    assert hi == 135 and lo < hi


def test_kanarazu_as_instruction_passes_but_as_claim_is_blocked():
    assert check_thread(t("作業の前には、必ず電源を切ってください。", "必ず止水栓を閉めてから。", "c"), [], C, CFG).ok
    assert not check_thread(t("給湯器は必ずサインを出す。", "b", "c"), [], C, CFG).ok


def test_one_character_idiom_is_not_a_claim():
    from xjido.verify import extract_numbers, _is_trivial
    nums = [n for n in extract_numbers("数字を1文字も間違えないことが大切です。") if not _is_trivial(n)]
    assert nums == []


def test_opener_and_iidashi(tmp_path, monkeypatch):
    from xjido import memory
    assert memory.opener("覚えておいて。エコキュートの家は") == "覚えておいて"
    assert memory.opener("ご存知でしたか？給湯器は") == "ご存知でしたか"
    p = tmp_path / "言い出し.md"
    p.write_text("# 言い出し\n\n## 本人が使った言い出し（毎週日曜に自動で足されます）\n\n- 「【見本】危険です」\n", encoding="utf-8")
    monkeypatch.setattr(memory, "IIDASHI", p)
    heads = [
        {"text": "覚えておいて。停電しても", "impression_count": 500},
        {"text": "ウォシュレットが1年以内に壊れる", "impression_count": 900},  # 数字入り・区切りなし → 入れない
        {"text": "断言します。業者は", "impression_count": 1200},
    ]
    memory.write_iidashi(heads)
    got = memory.iidashi_list()
    assert [s for s, _ in got] == ["断言します", "覚えておいて"]  # 見本は外れ、表示の多い順


def test_judge_drops_repeated_opener(monkeypatch):
    from xjido import generate
    from xjido.verify import Result
    monkeypatch.setattr(generate, "check_thread", lambda *a, **k: Result())
    cfg = {"確認": {"方式": "止まった時だけ確認", "確認待ちの最大件数_1日": 3, "AIで二重に確かめる": False}, "投稿": {"まとめるの最大_1日": 3}}
    def c(first):
        return {"posts": [first, "二つ目です。", "三つ目です。"], "sources": []}
    items = generate.judge(cfg, [c("覚えておいて。A"), c("覚えておいて。B"), c("危険です。C"), c("断言します。D")], 3, recent=["危険です。前の日の話"])
    st = [(i["posts"][0][:6], i["状態"]) for i in items]
    assert st[0][1] == "予約"
    assert st[1][1] == "捨てた" and "今日のほかの案" in items[1]["捨てた理由"]
    assert st[2][1] == "捨てた" and "直近の投稿" in items[2]["捨てた理由"]
    assert st[3][1] == "予約"


def test_judge_drops_material_used_in_last_week(monkeypatch):
    from xjido import generate
    from xjido.verify import Result
    monkeypatch.setattr(generate, "check_thread", lambda *a, **k: Result())
    cfg = {"確認": {"方式": "止まった時だけ確認", "確認待ちの最大件数_1日": 3, "AIで二重に確かめる": False}, "投稿": {"まとめるの最大_1日": 3}}
    q_items = [
        {"date": "2026-11-27", "状態": "投稿済み", "出どころ": [{"quote": "給湯器の標準交換は、98,000円から（税別）。"}]},
        {"date": "2026-11-10", "状態": "投稿済み", "出どころ": [{"quote": "日曜定休。"}]},  # 8日以上前 → 使ってよい
    ]
    used = generate.used_quotes(q_items, "2026-11-28")
    assert used == ["給湯器の標準交換は、98,000円から（税別）。"]
    a = {"posts": ["覚えておいて。A", "b", "c"], "sources": [{"quote": "給湯器の標準交換は、98,000円から（税別）。"}]}
    b = {"posts": ["断言します。B", "b", "c"], "sources": [{"quote": "日曜定休。"}]}
    items = generate.judge(cfg, [a, b], 2, recent=[], used=used)
    assert items[0]["状態"] == "捨てた" and "直近7日に同じ材料" in items[0]["捨てた理由"]
    assert items[1]["状態"] == "予約"


def test_short_quote_matches_line_with_source_url():
    from xjido.verify import Corpus
    c = Corpus("- 日曜定休。出どころ：https://x.com/a/status/1\n")
    assert c.has_quote("日曜定休。")
