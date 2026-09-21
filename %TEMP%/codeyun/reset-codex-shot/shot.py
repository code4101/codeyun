"""Temp probe: mint a dev admin token and screenshot the quota page charts."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(r"C:\home\chenkunze\slns\codeyun")
sys.path.insert(0, os.fspath(ROOT))

from sqlmodel import Session, select

from backend.core.access.auth import create_access_token
from backend.core.access.models import User
from backend.core.database import engine

OUT = Path(os.environ["TEMP"]) / "codeyun" / "reset-codex-shot"
OUT.mkdir(parents=True, exist_ok=True)

with Session(engine) as session:
    user = session.exec(select(User).where(User.is_superuser == True)).first()  # noqa: E712
    if user is None:
        user = session.exec(select(User)).first()
    if user is None:
        raise SystemExit("no user in dev db")
    print("user:", user.username, "superuser:", user.is_superuser)
    token = create_access_token({"sub": user.username})

from DrissionPage import ChromiumOptions, ChromiumPage

options = ChromiumOptions().auto_port()
options.set_argument("--window-size=1600,1400")
page = ChromiumPage(options)
page.get("http://localhost:5173/login")
page.run_js(f"localStorage.setItem('token', {token!r});")
page.get("http://localhost:5173/tools/reset-codex")
time.sleep(6)
png = OUT / "reset-codex.png"
page.get_screenshot(path=str(OUT), name="reset-codex", full_page=True)
print("shot:", sorted(p.name for p in OUT.iterdir()))
page.quit()
