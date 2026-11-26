"""AI の呼び出し。

提供元：
- anthropic …… Claude API（Anthropic SDK）。ANTHROPIC_API_KEY が要る。
- claude-cli …… 手元の `claude -p`。手元で試す時だけ（GitHub Actions では使わない）。
- file …… 開発用。AI を呼ばず、「依頼」をファイルに書き出し、人（や別の AI）が作った「返事」のファイルを読む。
  返事が無い時は LLMWait を投げて止まる。依頼と返事は 出力/ai/ に置く（XJIDO_AI_DIR で変えられる）。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile


class LLMError(RuntimeError):
    pass


class LLMWait(Exception):
    """提供元 file：返事のファイルがまだ無い。paths は、返事を置いてほしい場所。"""

    def __init__(self, paths: list[str]):
        super().__init__("返事のファイルを待っています：" + ", ".join(paths))
        self.paths = paths


def _ai_dir():
    from pathlib import Path

    from .config import ROOT

    d = Path(os.environ.get("XJIDO_AI_DIR") or ROOT / "出力" / "ai")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(tool: str, system: str, user: str, model: str | None, schema: dict | None) -> str:
    """依頼を書き出し、返事があればその中身を返す。無ければ LLMWait。"""
    import hashlib

    key = hashlib.sha1((system + "\n" + user).encode("utf-8")).hexdigest()[:8]
    d = _ai_dir()
    ext = "json" if schema else "md"
    req = d / f"依頼_{tool}_{key}.md"
    rep = d / f"返事_{tool}_{key}.{ext}"
    if rep.exists():
        return rep.read_text(encoding="utf-8")
    shape = (
        "\n\n---\n\n# 返し方\n\n次の JSON スキーマに合う JSON だけを、返事のファイルに書いてください（説明や ``` は付けない）。\n\n```json\n"
        + json.dumps(schema, ensure_ascii=False, indent=1) + "\n```\n"
        if schema
        else "\n\n---\n\n# 返し方\n\n本文だけを、返事のファイルに書いてください。\n"
    )
    req.write_text(
        f"# AI への依頼（{tool}）\n\n- モデルの指定：{model or '（なし）'}\n- 返事を置く場所：{rep}\n\n---\n\n# system\n\n{system}\n\n---\n\n# user\n\n{user}{shape}",
        encoding="utf-8",
    )
    raise LLMWait([str(rep)])


def _extract_json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise LLMError("AI の返事から JSON を取り出せませんでした：" + text[:200])
    return json.loads(text[a : b + 1])


def structured(cfg: dict, *, system: str, user: str, tool_name: str, description: str, schema: dict, model: str | None = None, max_tokens: int = 8000) -> dict:
    """決まった形（schema）の答えを返してもらう。"""
    provider = cfg["AI"]["提供元"]
    model = model or cfg["AI"]["モデル"]
    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": tool_name, "description": description, "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool_name:
                return dict(block.input)
        raise LLMError("AI が決まった形で答えませんでした（stop_reason=%s）" % resp.stop_reason)
    if provider == "file":
        return _extract_json(_file(tool_name, system, user, model, schema))
    if provider == "claude-cli":
        prompt = (
            f"{system}\n\n---\n\n{user}\n\n---\n\n"
            "答えは、次の JSON スキーマに合う JSON だけを出力してください（説明や ``` は付けない）。\n"
            + json.dumps(schema, ensure_ascii=False)
        )
        return _extract_json(_cli(prompt, model))
    raise LLMError(f"AI の提供元が正しくありません：{provider}")


def text(cfg: dict, *, system: str, user: str, model: str | None = None, max_tokens: int = 8000) -> str:
    provider = cfg["AI"]["提供元"]
    model = model or cfg["AI"]["モデル"]
    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic()
        resp = client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in resp.content if b.type == "text")
    if provider == "file":
        return _file("text", system, user, model, None)
    if provider == "claude-cli":
        return _cli(f"{system}\n\n---\n\n{user}", model)
    raise LLMError(f"AI の提供元が正しくありません：{provider}")


def _cli(prompt: str, model: str | None) -> str:
    cmd = ["claude", "-p", "--output-format", "text", "--tools", "", "--disable-slash-commands", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    with tempfile.TemporaryDirectory() as d:  # 手元のファイルを触らせないため、空のフォルダで動かす
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=d, timeout=900, env={**os.environ})
        except FileNotFoundError as e:
            raise LLMError("claude コマンドが見つかりません") from e
    if r.returncode != 0:
        raise LLMError("claude -p が失敗しました：" + (r.stderr or r.stdout)[:300])
    return r.stdout.strip()
