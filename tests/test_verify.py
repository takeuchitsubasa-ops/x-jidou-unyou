import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xjido import config  # noqa: E402
from xjido.verify import Corpus, check_thread, extract_numbers  # noqa: E402

CFG = config.load()
MEMORY = """
## ウォシュレットの故障
- 話：新品のウォシュレットが1年後に壊れた。塩素系の洗剤で基盤がやられることが多いと、メーカーのサービスマンから聞いた。
## 料金
- 訪問料金は中央市なら2,000円から、東市と西市なら3,000円から。
- 工事関連の資格は42件。
<!-- 例：水道代が月9,999円下がった -->
"""
CORPUS = Corpus(MEMORY)
SRC_PRICE = [{"expression": "中央市は2,000円から", "origin": "会社の事実", "quote": "中央市なら2,000円から、東市と西市なら3,000円から"}]
SRC_STORY = [{"expression": "洗剤で壊れた", "origin": "ネタ帳", "quote": "塩素系の洗剤で基盤がやられることが多いと、メーカーのサービスマンから聞いた"}]


def thread(*texts):
    return list(texts)


def test_number_with_source_passes():
    posts = thread("訪問の料金を先に出しています。", "中央市なら2,000円から、東市と西市なら3,000円からです。", "気になる時は写真を送ってください。")
    r = check_thread(posts, SRC_PRICE, CORPUS, CFG)
    assert r.ok, r.reasons
    assert r.has_claims


def test_number_without_source_is_blocked():
    posts = thread("水道代が月2,500円下がった家があります。", "蛇口を替えただけです。", "お試しください。")
    r = check_thread(posts, [], CORPUS, CFG)
    assert not r.ok
    assert any("2500円" in x for x in r.reasons)


def test_number_in_memory_but_not_cited_is_blocked():
    posts = thread("資格は42件あります。", "電気もガスも扱います。", "相談してください。")
    r = check_thread(posts, SRC_PRICE, CORPUS, CFG)  # 引用に 42件 が無い
    assert not r.ok
    assert any("42件" in x for x in r.reasons)


def test_same_number_other_unit_is_blocked():
    posts = thread("資格は42台あります。", "二つ目。", "三つ目。")
    r = check_thread(posts, [], CORPUS, CFG)
    assert not r.ok


def test_fake_quote_is_blocked():
    posts = thread("中央市なら2,000円から。", "二つ目。", "三つ目。")
    src = [{"expression": "x", "origin": "会社の事実", "quote": "中央市なら5,000円から受けています"}]
    r = check_thread(posts, src, CORPUS, CFG)
    assert not r.ok
    assert any("引用" in x for x in r.reasons)


def test_story_without_source_is_blocked_and_with_source_passes():
    posts = thread("先日、お客様から相談がありました。", "洗剤が原因でした。", "ご注意ください。")
    assert not check_thread(posts, [], CORPUS, CFG).ok
    assert check_thread(posts, SRC_STORY, CORPUS, CFG).ok


def test_no_numbers_no_story_passes():
    posts = thread("換気扇を回すと玄関のドアが重くなる家があります。", "空気の入り口が足りないサインです。", "気になる時は相談してください。")
    r = check_thread(posts, [], CORPUS, CFG)
    # 「家があります」は実話の書き方ではなく一般論として通す（実話は「先日」などの書き方で見分ける）
    assert r.ok, r.reasons
    assert not r.has_claims


def test_comment_numbers_in_memory_do_not_count():
    posts = thread("水道代が月9,999円下がりました。", "二つ目。", "三つ目。")
    r = check_thread(posts, [], CORPUS, CFG)
    assert not r.ok  # メモリの <!-- コメント --> の中の例は出どころにならない


def test_url_is_blocked():
    posts = thread("詳しくはこちら https://example.com/a", "二つ目。", "三つ目。")
    assert not check_thread(posts, [], CORPUS, CFG).ok


def test_wrong_thread_length_and_long_post():
    assert not check_thread(["一つだけ"], [], CORPUS, CFG).ok
    cfg = config.load()
    cfg["投稿"]["1投稿の最大文字数"] = 20
    assert not check_thread(["あ" * 30, "い", "う"], [], CORPUS, cfg).ok


def test_kanji_and_circled_numbers():
    assert [n.label() for n in extract_numbers("三人で、二万円。")] == ["3人", "2万円"]
    nums = extract_numbers("①真空引きは何分やりますか②配線は途中でつなぎませんか")
    assert nums == []
    assert [n.label() for n in extract_numbers("10〜15年が目安")] == ["10年", "15年"]


def test_short_quote_passes_only_as_whole_line():
    from xjido.verify import Corpus
    c = Corpus("- 日曜定休。\n- 電話は9時から17時。\n")
    assert c.has_quote("日曜定休。")
    assert not c.has_quote("日曜")
    assert not c.has_quote("17時")
