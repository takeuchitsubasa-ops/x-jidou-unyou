"""案を作る → 確かめる → 予約の列に入れる（朝の準備の中心）。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

from . import llm, memory
from .llm import LLMWait
from .config import JST
from .verify import Corpus, Result, check_thread

SCHEMA = {
    "type": "object",
    "properties": {
        "threads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "このスレッドの題（内部用。投稿には出ない）"},
                    "theme": {"type": "string", "description": "<今日のテーマ> から作った時は、そのテーマを一字も変えずに。テーマを使っていなければ空"},
                    "hook_type": {"type": "string", "description": "つかみの型。伸びる型.md の「文の形（つかみ）」にある言い出し（例：「最近気づいたんですが」）。無ければ型の名前"},
                    "kind": {"type": "string", "description": "投稿の種類。Tips／体験談／考え方／料金・数字 など"},
                    "source_note": {"type": "string", "description": "元にしたネタ帳の見出し、または会社の事実の項目。一般論なら「一般論」"},
                    "posts": {"type": "array", "items": {"type": "string"}, "description": "スレッドの投稿。1つ目＝つかみ、最後＝締め"},
                    "sources": {
                        "type": "array",
                        "description": "投稿の中の数字・料金・件数・体験談ごとの出どころ。数字も体験談も無ければ空にする",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expression": {"type": "string", "description": "投稿の中の言い方"},
                                "origin": {"type": "string", "enum": ["ネタ帳", "会社の事実", "テーマ"]},
                                "quote": {"type": "string", "description": "メモリの文章を、一字も変えずにそのまま写した所（数字を含む1文）"},
                            },
                            "required": ["expression", "origin", "quote"],
                        },
                    },
                },
                "required": ["title", "hook_type", "kind", "source_note", "posts", "sources"],
            },
        }
    },
    "required": ["threads"],
}

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"sentence": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["sentence", "reason"],
            },
        }
    },
    "required": ["findings"],
}


def length_range(cfg: dict) -> tuple[int | None, int, int]:
    """1投稿の字数の目安（全角で数えた字数）。お手本の平均に寄せ、上限より少し短くする。"""
    limit = cfg["投稿"]["1投稿の最大文字数"]
    hi = limit // 2 - 5
    avg = memory.otehon_avg_len()
    lo = (min(avg, hi) - 15) if avg else hi - 40
    return avg, max(lo, 20), hi


def system_prompt(cfg: dict) -> str:
    n = cfg["投稿"]["スレッドの長さ"]
    k = cfg["投稿"]["まとめるの最大_1日"]
    avg, lo, hi = length_range(cfg)
    avg_s = f"お手本の1投稿は、平均{avg}字です。" if avg else ""
    return f"""あなたは、中小企業の経営者本人に代わってXの投稿を書く担当です。本人のアカウントで伸びた投稿と同じ書き方で、本人の言葉と実績だけを使って書きます。作り話はしません。

書き方（いちばん大事）
A. <お手本> は、本人のアカウントで実際に伸びたスレッドの全文です。形・長さ・言い回し・文の終わり方（「だ・である」と「です・ます」の混ぜ方も）を、お手本に合わせます。読んだ人が、お手本と同じ人が書いたと思う文にします。お手本の話・数字・文の丸写しはしません。
B. 役割：1スレッドは{n}投稿です。お手本の1つ目・2つ目・3つ目が、それぞれ何をしているか（どこから入り、どこで切り、何を並べ、どう締めるか）を読み取り、同じ役割で書きます。お手本の1つ目が「…」で続きを読ませる所で切っていれば、同じように切ります。お手本の2つ目が①②③のように並べていれば、その形も使えます。
C. 長さ：{avg_s}各投稿を{lo}〜{hi}字で書きます。短すぎると中身が薄く見え、{hi}字を超えると投稿できません。
D. つかみ：1つ目は、<言い出しの候補> か <お手本> の1つ目にある言い出し（最初の一言）で始めます。<直近の投稿> の冒頭で使われた言い出しは使いません。今日の案どうしでも、同じ言い出しは使いません。中身の言葉が入った言い出し（「玄関のドアが重い家は」など）は、形だけ借ります（「〇〇な家は」）。気持ちを言う言い出し（「怒ってます」「泣きそうです」など）は、ネタ帳かテーマにその元になる話がある時だけ使います。hook_type には、使った言い出しをそのまま書きます。
D2. 種類：今日の案どうしで、種類（kind）を重ねません。種類は、設備の豆知識／危ない話（注意）／業者選び・見積もり／現場であった話／お金・料金 などから選び、同じ日に同じ種類を2本作りません。
E. 締め：締め方もお手本に合わせます。ただし「まとめると」で締めてよいのは、全体で{k}スレッドまでです。締めを問いかけにするのは、1日1スレッドまでです。
F. 中身：暮らしの中で困る場面から入り、原因・見分け方・対処を、部品の名前・場所・やることで具体的に書きます。読んだ人が今日できることで終えます。

テーマ
G. <今日のテーマ> があれば、1テーマにつき1スレッドを、テーマの順に作り、theme にそのテーマを一字も変えずに書きます。テーマの「：」の後ろの補足は本人の言葉なので、材料として使えます（出どころの origin は「テーマ」）。テーマの分を作った残りは、材料（ネタ帳・会社の事実）の話から作り、theme は空にします。

材料の決まり
1. 数字（料金・金額・件数・台数・年数・割合・時間・容量・型番）と体験談（「先日」「お客様から」「〜な家があった」）は、<ネタ帳>・<会社の事実>・<今日のテーマ> に書いてある物だけを、書いてあるとおりに使います。それ以外の数字は、一般によく知られた数字でも書きません。計算して出した数字も書きません。
2. 数字も体験談も使わない一般的な説明（仕組み・見分け方・注意点・選び方）は書いてかまいません。安全・法令・電気・ガスに関わる内容で自信が無い所は書きません。
3. 「うちでは〜している」「うちは〜」のような会社のやり方は、材料にそう書いてある時だけ書きます。
4. 「最近気づいたんですが」のように本人の体験を言う言い出しは、ネタ帳かテーマにその体験がある時だけ使います。材料に無い本人の習慣・気持ち・気づきは足しません。お手本に無い比喩も使いません。
5. 出どころ（sources）：投稿に数字・体験談・会社のやり方を入れたら、その1つごとに、材料の文章を一字も変えずに写した quote を付けます。写せない物は、投稿に入れません。
6. URL・ハッシュタグ・絵文字は書きません。
7. <直近の投稿> と同じ話・同じつかみの繰り返しは避けます。<伸びる型> の「落ちた型」「避けること」は使いません。
8. テーマが無い日は、材料（ネタ帳と会社の事実）の話を使った案を、全体の半分以上にします。"""


def user_prompt(n: int, recent: list[str], hot: list[str], themes: list[str] | None = None, used: list[str] | None = None) -> str:
    themes = themes or []
    used = used or []
    theme_s = "\n".join(f"{i}. {t}" for i, t in enumerate(themes, 1)) or "（今日はテーマがありません。材料から選んでください）"
    return f"""<お手本（本人のアカウントで伸びたスレッドの全文。書き方だけをまねる）>
{memory.otehon_for_prompt() or '（まだありません）'}
</お手本>

<今日のテーマ>
{theme_s}
</今日のテーマ>

<言い出しの候補（本人が実際に使った言い出し。表示の多い順）>
{memory.iidashi_for_prompt() or '（まだありません。お手本の言い出しを使ってください）'}
</言い出しの候補>

<伸びる型>
{memory.read(memory.KATA)}
</伸びる型>

<ネタ帳>
{memory.read(memory.NETA)}
</ネタ帳>

<会社の事実>
{memory.read(memory.FACTS)}
</会社の事実>

<直近7日に使った材料（今日は使わない。同じ話に見えるため）>
{chr(10).join('- ' + u for u in used) or '（まだありません）'}
</直近7日に使った材料>

<直近の投稿（冒頭。同じ話を重ねない）>
{chr(10).join('- ' + r for r in recent) or '（まだありません）'}
</直近の投稿>

<直近で表示が多かった投稿（参考）>
{chr(10).join('- ' + h for h in hot) or '（数字はまだ取れていません）'}
</直近で表示が多かった投稿>

上の決まりに従って、{n}つのスレッドの案を作ってください。"""


def audit_thread(cfg: dict, corpus_text: str, posts: list[str]) -> list[dict]:
    """AI に、材料に無い具体的な主張が無いかだけを見てもらう（二重の確かめ）。"""
    out = llm.structured(
        cfg,
        system=(
            "あなたは校閲係です。投稿の中に、<材料> に書かれていない具体的な事実の主張が無いかだけを見ます。"
            "具体的な事実とは、数字・金額・件数・年数・割合・型番・日付、本人や会社や特定のお客様の体験・実績の主張です。"
            "仕組みや注意点の一般的な説明は指摘しません。指摘が無ければ findings は空にします。"
        ),
        user=f"<材料>\n{corpus_text}\n</材料>\n\n<投稿>\n" + "\n---\n".join(posts) + "\n</投稿>",
        tool_name="report_findings",
        description="材料に無い具体的な主張の一覧を報告する",
        schema=AUDIT_SCHEMA,
        model=cfg["AI"]["確認用のモデル"],
        max_tokens=2000,
    )
    return out.get("findings", [])


def plan_date(now: datetime, cfg: dict) -> str:
    """どの日の投稿を準備するか。その日の最後の枠（遅れの許容も含む）を過ぎていなければ今日、過ぎていれば明日。"""
    last = sorted(cfg["投稿"]["時刻"])[-1]
    h, m = map(int, last.split(":"))
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    day = now.date() if now <= end else now.date() + timedelta(days=1)
    return day.isoformat()


def item_id(posts: list[str]) -> str:
    return hashlib.sha1("\n".join(posts).encode("utf-8")).hexdigest()[:8]


def make_candidates(cfg: dict, n: int, recent: list[str], hot: list[str], themes: list[str] | None = None, used: list[str] | None = None) -> list[dict]:
    res = llm.structured(
        cfg,
        system=system_prompt(cfg),
        user=user_prompt(n, recent, hot, themes, used),
        tool_name="submit_threads",
        description="作ったスレッドの案を提出する",
        schema=SCHEMA,
        max_tokens=12000,
    )
    return res.get("threads", [])


def _qkey(q: str) -> str:
    """材料の引用を比べる時の形（空白と句読点を除く。短すぎる物は比べない）。"""
    k = re.sub(r"[\s、。「」]", "", q or "")
    return k if len(k) >= 4 else ""


def used_quotes(items: list[dict], plan_day: str, days: int = 7) -> list[str]:
    """予約の列から、直近 days 日に予約・投稿した案の材料の引用を集める。"""
    from datetime import date, timedelta
    start = (date.fromisoformat(plan_day) - timedelta(days=days)).isoformat()
    out: list[str] = []
    for it in items:
        if start <= (it.get("date") or "") < plan_day and it.get("状態") in ("予約", "確認待ち", "確認済み", "一部投稿済み", "投稿済み"):
            for x in it.get("出どころ", []):
                q = x.get("quote", "")
                if q and q not in out:
                    out.append(q)
    return out


def judge(cfg: dict, cands: list[dict], need: int, recent: list[str] | None = None, used: list[str] | None = None) -> list[dict]:
    """案を確かめて、予約・確認待ち・捨てたに振り分ける。直近の投稿や今日のほかの案と同じ言い出しの案、直近7日と同じ材料の案は捨てる。"""
    recent_op = {memory.opener(r) for r in (recent or [])}
    used_q = {_qkey(u) for u in (used or []) if _qkey(u)}
    today_op: set[str] = set()
    mode = cfg["確認"]["方式"]
    corpus_text = memory.corpus_text()
    corpus = Corpus(corpus_text)
    k = cfg["投稿"]["まとめるの最大_1日"]
    out, matome, ready, held = [], 0, 0, 0
    waiting: list[str] = []
    hold_cap = max(cfg["確認"]["確認待ちの最大件数_1日"], need if mode == "全部確認する" else 0)
    for c in cands:
        posts = [p.strip() for p in c.get("posts", [])]
        res: Result = check_thread(posts, c.get("sources", []), corpus, cfg)
        if res.ok and cfg["確認"]["AIで二重に確かめる"]:
            try:
                for f in audit_thread(cfg, corpus_text, posts):
                    res.fail(f"AI の確認で指摘：「{f['sentence'][:40]}」{f['reason']}")
            except LLMWait as w:  # 開発用（提供元: file）：返事のファイルを待つ
                waiting += w.paths
                continue
            except Exception as e:  # AI の確認が動かない時は、安全側（止める）に倒す
                res.fail(f"AI の確認が動きませんでした：{e}")
        item = {
            "id": item_id(posts),
            "題": c.get("title", ""),
            "テーマ": (c.get("theme") or "").strip(),
            "型": c.get("hook_type", ""),
            "種類": c.get("kind", ""),
            "ネタ元": c.get("source_note", ""),
            "posts": posts,
            "出どころ": c.get("sources", []),
            "検査": {"ok": res.ok, "理由": res.reasons, "数字": res.numbers},
            "issue": None,
            "tweet_ids": [],
        }
        uses_matome = bool(posts) and posts[-1].startswith("まとめると")
        op = memory.opener(posts[0]) if posts else ""
        dup_q = next((x.get("quote", "") for x in c.get("sources", []) if _qkey(x.get("quote", "")) in used_q), None)
        if dup_q:
            item["状態"] = "捨てた"
            item["捨てた理由"] = f"直近7日に同じ材料を使っています（「{dup_q[:30]}」）"
        elif op and op in recent_op:
            item["状態"] = "捨てた"
            item["捨てた理由"] = f"直近の投稿と同じ言い出しです（「{op}」）"
        elif op and op in today_op:
            item["状態"] = "捨てた"
            item["捨てた理由"] = f"今日のほかの案と同じ言い出しです（「{op}」）"
        elif uses_matome and matome >= k:
            item["状態"] = "捨てた"
            item["捨てた理由"] = f"「まとめると」で締める案が{k}本を超えました"
        elif mode == "全部確認する":
            item["状態"] = "確認待ち" if held < hold_cap else "捨てた"
            item["確認の理由"] = "設定が「全部確認する」です" + ("" if res.ok else "（確かめでも止まりました）")
            if item["状態"] == "捨てた":
                item["捨てた理由"] = "確認待ちの上限を超えました"
        elif res.ok:
            item["状態"] = "予約" if ready < need else "捨てた"
            if item["状態"] == "捨てた":
                item["捨てた理由"] = "予約の枠がいっぱいでした（予備の案）"
        elif mode == "止まった案は捨てる":
            item["状態"] = "捨てた"
            item["捨てた理由"] = "出どころの確かめが通りませんでした（設定が「止まった案は捨てる」なので確認待ちにせず捨てました）"
        else:
            item["状態"] = "確認待ち" if held < hold_cap else "捨てた"
            item["確認の理由"] = "；".join(res.reasons)
            if item["状態"] == "捨てた":
                item["捨てた理由"] = "確認待ちの上限を超えました"
        if item["状態"] == "予約":
            ready += 1
            matome += uses_matome
            today_op.add(op)
        elif item["状態"] == "確認待ち":
            held += 1
            matome += uses_matome
            today_op.add(op)
        out.append(item)
    if waiting:
        raise LLMWait(waiting)
    return out


def hold_issue_body(item: dict, cfg: dict) -> str:
    posts = "\n\n".join(f"**{i}つ目**\n{p}" for i, p in enumerate(item["posts"], 1))
    srcs = "\n".join(f"- {s['expression']} ← {s['origin']}：「{s['quote']}」" for s in item.get("出どころ", [])) or "- （挙げられた出どころはありません）"
    return (
        f"このスレッドは、出どころを確かめられなかったので、投稿せずに止めています。\n\n"
        f"**止めた理由**\n{item.get('確認の理由', '')}\n\n"
        f"---\n{posts}\n---\n\n**AI が挙げた出どころ**\n{srcs}\n\n"
        f"**どうするか**\n- そのまま出してよい（数字や話が本当）：この Issue に、ラベル「{cfg['確認']['OKラベル']}」を付けてください。次の投稿の時間に出ます。\n"
        f"- 出さない：この Issue をそのまま閉じてください。\n"
        f"- 数字や話が本当なら、ネタ帳に書き足しておくと、次から確認なしで使えます（Issue に「ネタ」ラベルで書いても入ります）。\n"
    )


def render_output(plan_day: str, items: list[dict], cfg: dict, header_extra: str = "") -> str:
    lines = [f"# {plan_day} の投稿の案（試し運転）", "", f"作った時刻：{datetime.now(JST):%Y-%m-%d %H:%M}　確認の設定：{cfg['確認']['方式']}　モデル：{cfg['AI']['モデル']}", ""]
    if header_extra:
        lines += [header_extra, ""]
    times = sorted(cfg["投稿"]["時刻"])
    slot = 0
    for it in items:
        label = it["状態"]
        if label == "予約" and slot < len(times):
            label = f"予約（{times[slot]} に投稿）"
            slot += 1
        lines += [f"## {it['題']}　［{label}］", ""]
        if it.get("テーマ"):
            lines.append(f"- テーマ：{it['テーマ']}")
        lines.append(f"- つかみの型：{it['型']}　／　種類：{it['種類']}　／　元の話：{it['ネタ元']}")
        for i, p in enumerate(it["posts"], 1):
            lines += ["", f"**{i}つ目**", "", p]
        lines += ["", "**確かめの結果**", ""]
        lines.append(f"- 判定：{'通った' if it['検査']['ok'] else '止めた'}")
        for r in it["検査"]["理由"]:
            lines.append(f"- 理由：{r}")
        for n in it["検査"]["数字"]:
            lines.append(f"- 数字 {n['数字']}：{'メモリにあり、引用もあり' if n['通った'] else 'メモリに無い、または引用が無い'}")
        for s in it["出どころ"]:
            lines.append(f"- 出どころ（{s['origin']}）：{s['expression']} ← 「{s['quote']}」")
        if it.get("確認の理由") and it["状態"] == "確認待ち":
            lines.append(f"- 確認待ちにした理由：{it['確認の理由']}")
        if it.get("捨てた理由"):
            lines.append(f"- 捨てた理由：{it['捨てた理由']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def recent_openers(rows: list[dict], n: int = 30) -> list[str]:
    seen = []
    for r in rows[-200:]:
        t = re.sub(r"\s+", " ", r.get("text") or r.get("本文") or "")
        if t:
            seen.append(t[:50])
    return seen[-n:]


def hot_posts(rows: list[dict], n: int = 5) -> list[str]:
    rows = [r for r in rows if r.get("impression_count") is not None]
    rows.sort(key=lambda r: -r["impression_count"])
    return [f"{r['impression_count']}表示：{re.sub(chr(10)+'+', ' ', r.get('text',''))[:50]}" for r in rows[:n]]
