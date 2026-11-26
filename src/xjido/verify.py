"""確かめ（いちばん大事な所）。

投稿の中の数字・料金・件数・「〜な家があった」のような実話は、
メモリ（ネタ帳・会社の事実）に出どころがある物だけ通す。

- 数字：投稿から数字（単位つき）を抜き出し、メモリの中にある数字と照らす。
- 引用：AI が挙げた「出どころの引用」が、メモリの本文にそのまま有るかを見る。
- 実話：「先日」「お客様から」などの書き方があるのに、ネタ帳の出どころが無ければ止める。
- URL：URL 入りの投稿は X API の料金が高いので止める。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .textutil import has_url, norm, weighted_len

# 単位（NFKC 後の形）。長い物から先に当てる。
UNITS = sorted(
    [
        "万円", "円", "台", "件", "年間", "年前", "年", "か月", "ヶ月", "カ月", "ヵ月", "月", "日", "時間", "時", "分", "秒",
        "人", "名", "回", "割", "倍", "%", "個", "本", "枚", "社", "軒", "歳", "度", "kg", "m3", "m2", "cm", "mm",
        "km", "m", "L", "リットル", "V", "A", "kW", "W", "°C", "畳", "号", "階", "段", "つ", "番", "点", "種類", "世帯",
        "棟", "区", "市", "文字", "週間", "週", "万", "千", "億", "兆", "箇所", "ヶ所", "カ所", "ヵ所",
    ],
    key=len,
    reverse=True,
)
_UNIT_ALIAS = {"ヶ月": "か月", "カ月": "か月", "ヵ月": "か月", "名": "人", "年間": "年", "ヶ所": "箇所", "カ所": "箇所", "ヵ所": "箇所"}
# 数が小さい「1つ」「2回」のような言い方は、事実の主張ではないので通す
_TRIVIAL_UNITS = {"つ", "回", "度", "枚", "点", "個", "番", "種類", "文字"}

_KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_UNITS = ("万円", "円", "台", "件", "年", "か月", "ヶ月", "ヵ月", "カ月", "日", "時間", "分", "人", "名", "回", "割", "倍", "個", "本", "枚", "社", "軒", "歳", "世帯")
_KANJI_RE = re.compile(r"([〇一二三四五六七八九十百千]+)(?=(?:" + "|".join(map(re.escape, _KANJI_UNITS)) + "))")

_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)")
_TOKEN = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)(?:\s*(?:〜|~|-|ー|から)\s*(\d[\d,]*(?:\.\d+)?))?\s*(" + "|".join(map(re.escape, UNITS)) + r")?"
)

STORY = re.compile(
    r"(先日|昨日|一昨日|昨夜|昨晩|今朝|先週|先月|去年|昨年|最近|この前|この間|以前|\d+年前|ある日|ある時|あるお客|"
    r"お客(?:様|さま|さん)から|お客(?:様|さま|さん)に|ご依頼|依頼があり|依頼を受け|相談があり|相談が来|問い合わせが|"
    r"と言われ(?:ました|た)|と聞かれ(?:ました|た)|と頼まれ|(?:家|お宅|現場)があり?(?:ました|った)|経験があり)"
)
# 会社のやり方の言い方。材料（会社の事実・ネタ帳・テーマ）の出どころがある時だけ通す
COMPANY = re.compile(r"(うちでは|うちは|うちの会社|弊社では|弊社は|当社では|当社は)")
# 量の言い方。数字と同じように、出どころの引用の中に同じ言葉があるときだけ通す
QUANT = re.compile(r"(半年|半分|倍|ほとんど|全員|必ず|ほぼ全て|ほぼ全部)")


def _kanji_to_int(s: str) -> int:
    total, cur = 0, 0
    for ch in s:
        if ch in _KANJI:
            cur = cur * 10 + _KANJI[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "千":
            total += (cur or 1) * 1000
            cur = 0
    return total + cur


def _prepare(text: str) -> str:
    t = re.sub(r"[①-⑳⑴-⒇❶-❿]", "", text)  # 丸数字の見出しは数字として数えない
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"(?m)^\s*\d+[.)]\s*", "", t)  # 行頭の「1.」「2)」も数えない
    t = _KANJI_RE.sub(lambda m: str(_kanji_to_int(m.group(1))), t)
    return t


@dataclass(frozen=True)
class Num:
    value: str  # カンマなし
    unit: str
    raw: str

    def label(self) -> str:
        return f"{self.value}{self.unit}"


def extract_numbers(text: str) -> list[Num]:
    t = _prepare(text)
    out: list[Num] = []
    for m in _TOKEN.finditer(t):
        a, b, unit = m.group(1), m.group(2), m.group(3) or ""
        unit = _UNIT_ALIAS.get(unit, unit)
        for v in (a, b):
            if v:
                out.append(Num(v.replace(",", ""), unit, m.group(0)))
    return out


def _is_trivial(n: Num) -> bool:
    try:
        v = float(n.value)
    except ValueError:
        return False
    return v <= 3 and n.unit in _TRIVIAL_UNITS


def strip_corpus(text: str) -> str:
    """メモリの本文から、コメント（<!-- -->）とURLを除く。説明用の例の数字や、URLの中の数字で照合が通らないようにする。"""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = re.sub(r"https?://\S+", "", t)
    # 「要本人確認」の行（まだ本人が見ていない物）と、「出どころ：」の行は、照合の材料にしない
    t = "\n".join(ln for ln in t.splitlines() if "要本人確認" not in ln and not re.match(r"\s*-?\s*出どころ[：:]", ln))
    return t


class Corpus:
    def __init__(self, text: str):
        self.text = strip_corpus(text)
        self.norm = norm(self.text)
        nums = extract_numbers(self.text)
        self.pairs = {(n.value, n.unit) for n in nums}
        self.values = {n.value for n in nums}

    def has_number(self, n: Num) -> bool:
        if n.unit:
            return (n.value, n.unit) in self.pairs
        return n.value in self.values

    def has_quote(self, quote: str) -> bool:
        q = norm(strip_corpus(quote))
        if len(q) >= 6:
            return q in self.norm
        # 短い引用（「日曜定休。」など）は、メモリの1行まるごとと同じ時だけ通す（「年」「円」のような切れ端で通らないように）
        return bool(q) and q in self.lines

    @property
    def lines(self) -> set[str]:
        out = set()
        for ln in self.text.splitlines():
            ln = re.sub(r"^\s*(?:[-*・]|\d+[.)])\s*", "", ln)
            ln = re.split(r"出どころ[:：]", ln)[0]  # 行の後ろの「出どころ：URL」は、引用の照合に入れない
            if ln.strip():
                out.add(norm(ln))
        return out


@dataclass
class Result:
    ok: bool = True
    reasons: list[str] = field(default_factory=list)
    numbers: list[dict] = field(default_factory=list)  # {"数字","通った","出どころ"}
    has_claims: bool = False  # 数字か実話の書き方が入っているか

    def fail(self, reason: str) -> None:
        self.ok = False
        self.reasons.append(reason)


def _is_instruction(body: str, m: re.Match) -> bool:
    """「必ず電源を切ってください」のような、やってほしいことの言い方は、量の主張ではないので通す。"""
    if m.group(1) != "必ず":
        return False
    rest = re.split(r"[。！？\n]", body[m.end():], maxsplit=1)[0]
    return bool(re.search(r"(てください|でください|て$|で$|こと$|ましょう|てから|ように|ないで)", rest))


def check_thread(posts: list[str], sources: list[dict], corpus: Corpus, cfg: dict) -> Result:
    """1つのスレッド（投稿の並び）を確かめる。

    sources: AI が挙げた出どころ。 [{"expression": 投稿の中の言い方, "origin": "ネタ帳"|"会社の事実"|"テーマ", "quote": メモリからの引用}]
    """
    res = Result()
    limit = cfg["投稿"]["1投稿の最大文字数"]
    want = cfg["投稿"]["スレッドの長さ"]
    if len(posts) != want:
        res.fail(f"投稿の数が{want}ではありません（{len(posts)}）")
    for i, p in enumerate(posts, 1):
        if not p.strip():
            res.fail(f"{i}つ目の投稿が空です")
        if weighted_len(p) > limit:
            res.fail(f"{i}つ目の投稿が長すぎます（{weighted_len(p)}／{limit}）")
        if has_url(p):
            res.fail(f"{i}つ目の投稿にURLがあります（X API はURL入りの投稿が高いので入れません）")

    valid_sources = []
    for s in sources or []:
        q = s.get("quote", "")
        if corpus.has_quote(q):
            valid_sources.append(s)
        else:
            res.fail(f"出どころの引用がメモリにありません：「{q[:30]}」")
    quoted = Corpus("\n".join(s.get("quote", "") for s in valid_sources)) if valid_sources else None

    body = "\n".join(posts)
    for n in extract_numbers(body):
        if _is_trivial(n):
            continue
        res.has_claims = True
        in_memory = corpus.has_number(n)
        # 数字は「メモリにある」だけでなく、AI が挙げた引用の中にも有ることを求める（出どころを1つずつ挙げさせる）
        cited = bool(quoted and quoted.has_number(n))
        res.numbers.append({"数字": n.label(), "通った": in_memory and cited, "メモリにある": in_memory, "引用にある": cited})
        if not in_memory:
            res.fail(f"出どころの無い数字：{n.label()}")
        elif not cited:
            res.fail(f"数字の出どころ（引用）が挙げられていません：{n.label()}")

    for w in sorted({m.group(1) for m in QUANT.finditer(body) if not _is_instruction(body, m)}):
        res.has_claims = True
        if not (quoted and w in quoted.text):
            res.fail(f"出どころの無い言い方：「{w}」（材料の文に、同じ言葉が要ります）")

    m = STORY.search(_prepare(body))
    if m:
        res.has_claims = True
        if not any(s.get("origin") in ("ネタ帳", "テーマ") for s in valid_sources):
            res.fail(f"実話の書き方（「{m.group(1)}」）なのに、ネタ帳かテーマの出どころがありません")
    m = COMPANY.search(body)
    if m:
        res.has_claims = True
        if not valid_sources:
            res.fail(f"会社のやり方の書き方（「{m.group(1)}」）なのに、出どころがありません")
    return res
