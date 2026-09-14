import httpx
from sqlmodel import Session, select
from backend.db import engine
from backend.models import User
from backend.core.access.auth import create_access_token
import json

with Session(engine) as session:
    user = session.exec(select(User)).first()

token = create_access_token(data={"sub": user.username})

headers = {"Authorization": f"Bearer {token}"}

# 1. Stop current task
resp = httpx.post("http://localhost:8000/api/fanxiu/kernel-scheduler/task/stop", headers=headers)
print("Stop Task:", resp.status_code, resp.text)

# 2. Disable engineering scheduler (take AI control)
settings = httpx.get("http://localhost:8000/api/fanxiu/kernel-scheduler/settings", headers=headers).json()
settings["job_group_enabled"] = False
resp = httpx.put("http://localhost:8000/api/fanxiu/kernel-scheduler/settings", headers=headers, json=settings)
print("Disable Scheduler:", resp.status_code, resp.text)

# 3. Get status
resp = httpx.get("http://localhost:8000/api/fanxiu/kernel-scheduler/status", headers=headers)
print("Status:", json.dumps(resp.json(), ensure_ascii=False, indent=2))