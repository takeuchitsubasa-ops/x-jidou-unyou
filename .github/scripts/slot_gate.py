"""今は投稿の枠か、をシェルで判定する（pip install の前に動かして、枠でなければ早く終えるため）。
標準ライブラリだけで動く。設定.yml の「時刻」「遅れの許容_分」と、data/投稿記録.jsonl（使った枠）を読む。
結果は、GITHUB_OUTPUT に go=true / go=false で書く。"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
root = Path(__file__).resolve().parents[2]
conf = (root / "設定.yml").read_text(encoding="utf-8")
times = re.findall(r"(\d{1,2}:\d{2})", re.search(r"時刻:\s*\[([^\]]*)\]", conf).group(1))
m = re.search(r"遅れの許容_分:\s*(\d+)", conf)
tol = int(m.group(1)) if m else 180
now = datetime.now(JST)
used = set()
log = root / "data" / "投稿記録.jsonl"
if log.exists():
    for ln in log.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            used.add(json.loads(ln).get("枠"))
go = False
for t in times:
    h, mi = map(int, t.split(":"))
    start = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if start <= now <= start + timedelta(minutes=tol) and f"{now.date()} {t.zfill(5)}" not in used:
        go = True
print(f"今 {now:%Y-%m-%d %H:%M}（日本時間）／枠 {times}／使った枠 {sorted(x for x in used if x and x.startswith(str(now.date())))} → {'投稿の枠です' if go else '今は枠ではありません'}")
with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a", encoding="utf-8") as f:
    f.write(f"go={'true' if go else 'false'}\n")
