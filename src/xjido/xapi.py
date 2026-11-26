"""X API v2（OAuth 1.0a ユーザーコンテキスト）。

使う物：
- GET  /2/users/me                 自分のID（最初の1回だけ。結果は data/アカウント.json に残す）
- GET  /2/users/:id/tweets         自分の最近の投稿と数字（public_metrics に impression_count を含む）
- POST /2/tweets                   投稿（スレッドは reply.in_reply_to_tweet_id でつなぐ）
鍵は環境変数（GitHub の Secrets）から読む：X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import requests
from requests_oauthlib import OAuth1Session

API = "https://api.x.com/2"
KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")


class XError(RuntimeError):
    pass


def have_keys(env=os.environ) -> bool:
    return all(env.get(k) for k in KEYS)


class XClient:
    def __init__(self, env=os.environ):
        missing = [k for k in KEYS if not env.get(k)]
        if missing:
            raise XError("X の鍵が足りません：" + ", ".join(missing))
        self.s = OAuth1Session(env["X_API_KEY"], env["X_API_SECRET"], env["X_ACCESS_TOKEN"], env["X_ACCESS_TOKEN_SECRET"])
        self.calls = {"GET": 0, "POST": 0}
        self.posts_read = 0

    def _req(self, method: str, path: str, **kw) -> dict:
        for attempt in range(4):
            r = self.s.request(method, API + path, timeout=30, **kw)
            self.calls[method] = self.calls.get(method, 0) + 1
            if r.status_code == 429:  # 回数の上限。待ってやり直す（投稿は二重にならないよう、429 の時だけ）
                wait = int(r.headers.get("x-rate-limit-reset", "0")) - int(time.time())
                time.sleep(min(max(wait, 5), 60))
                continue
            if r.status_code >= 500 and method == "GET":
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise XError(f"X API {method} {path} が失敗しました（{r.status_code}）：{r.text[:300]}")
            return r.json()
        raise XError(f"X API {method} {path} が繰り返し失敗しました")

    def me(self) -> dict:
        return self._req("GET", "/users/me")["data"]

    def my_posts(self, user_id: str, since: datetime, limit: int = 100) -> list[dict]:
        """自分の投稿（返信を含む）と数字。since 以降、新しい順に limit 件まで。"""
        out: list[dict] = []
        token = None
        while len(out) < limit:
            params = {
                "max_results": max(5, min(100, limit - len(out))),
                "tweet.fields": "created_at,public_metrics,referenced_tweets,conversation_id,in_reply_to_user_id",
                "start_time": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if token:
                params["pagination_token"] = token
            data = self._req("GET", f"/users/{user_id}/tweets", params=params)
            rows = data.get("data", [])
            self.posts_read += len(rows)
            out.extend(rows)
            token = data.get("meta", {}).get("next_token")
            if not token or not rows:
                break
        return out[:limit]

    def post(self, text: str, reply_to: str | None = None) -> str:
        body: dict = {"text": text}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        return self._req("POST", "/tweets", json=body)["data"]["id"]


def since_days(days: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)
