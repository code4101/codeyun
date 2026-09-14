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

resp = httpx.get("http://localhost:8000/api/fanxiu/data-annotation/asset-tree", headers=headers)
print(resp.text)
