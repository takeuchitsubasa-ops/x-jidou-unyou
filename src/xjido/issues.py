"""GitHub の Issue の読み書き（ネタの取り込み・確認待ち）。GITHUB_TOKEN と GITHUB_REPOSITORY を環境変数から読む。"""
from __future__ import annotations

import os

import requests

API = "https://api.github.com"
LABEL_NETA = "ネタ"
LABEL_THEME = "テーマ"
LABEL_HOLD = "確認待ち"
LABEL_FAIL = "投稿の失敗"
LABEL_NOTICE = "お知らせ"


def available(env=os.environ) -> bool:
    return bool(env.get("GITHUB_TOKEN") and env.get("GITHUB_REPOSITORY"))


class Issues:
    def __init__(self, env=os.environ):
        self.repo = env["GITHUB_REPOSITORY"]
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {env['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})

    def _url(self, p: str) -> str:
        return f"{API}/repos/{self.repo}{p}"

    def ensure_label(self, name: str, color: str = "0e8a16", desc: str = "") -> None:
        r = self.s.get(self._url(f"/labels/{name}"), timeout=30)
        if r.status_code == 404:
            self.s.post(self._url("/labels"), json={"name": name, "color": color, "description": desc}, timeout=30).raise_for_status()

    def ensure_all(self, ok_label: str) -> None:
        """使うラベルを、最初に全部作る（無いと、Issue の作成や「OK」の判定が止まる）。"""
        self.ensure_label(LABEL_NETA, "1d76db", "現場の話。翌朝、ネタ帳に取り込まれる")
        self.ensure_label(LABEL_THEME, "0052cc", "書いてほしい話の1行。翌朝、テーマに入り、その話でスレッドが作られる")
        self.ensure_label(ok_label, "0e8a16", "確認待ちのスレッドを投稿してよい")
        self.ensure_label(LABEL_HOLD, "fbca04", "出どころを確かめられず、投稿を止めている物")
        self.ensure_label(LABEL_FAIL, "d73a4a", "投稿に失敗した")
        self.ensure_label(LABEL_NOTICE, "5319e7", "お知らせ（伸びる型の大きな変更など）")

    def list(self, label: str, state: str = "open") -> list[dict]:
        out, page = [], 1
        while True:
            r = self.s.get(self._url("/issues"), params={"labels": label, "state": state, "per_page": 100, "page": page}, timeout=30)
            r.raise_for_status()
            rows = [x for x in r.json() if "pull_request" not in x]
            out.extend(rows)
            if len(r.json()) < 100:
                return out
            page += 1

    def create(self, title: str, body: str, labels: list[str]) -> int:
        r = self.s.post(self._url("/issues"), json={"title": title, "body": body, "labels": labels}, timeout=30)
        r.raise_for_status()
        return r.json()["number"]

    def comment(self, number: int, body: str) -> None:
        self.s.post(self._url(f"/issues/{number}/comments"), json={"body": body}, timeout=30).raise_for_status()

    def close(self, number: int, comment: str | None = None) -> None:
        if comment:
            self.comment(number, comment)
        self.s.patch(self._url(f"/issues/{number}"), json={"state": "closed"}, timeout=30).raise_for_status()

    def labels_of(self, number: int) -> list[str]:
        r = self.s.get(self._url(f"/issues/{number}"), timeout=30)
        r.raise_for_status()
        return [x["name"] for x in r.json().get("labels", [])]
