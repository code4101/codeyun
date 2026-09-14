import sys
import json
from sqlmodel import Session, select
from backend.db import engine
from backend.core.jobs.models import LocalJob

with Session(engine) as session:
    stmt = select(LocalJob).where(LocalJob.job_type.like("fanxiu%"))
    jobs = session.exec(stmt).all()
    results = []
    for job in jobs:
        results.append({
            "id": job.id,
            "job_type": job.job_type,
            "name": job.name,
            "status": job.status,
            "next_time": str(job.next_time) if job.next_time else None,
            "last_error": job.last_error
        })
    print(json.dumps(results, indent=2, ensure_ascii=False))