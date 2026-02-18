from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.task_manager import router as task_router
from api.agent import router as agent_router
from api.filesystem import router as filesystem_router
import uvicorn
import os

app = FastAPI(title="CodeYun Backend", description="Local backend for CodeYun tools")

# Allow CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For local development, allow all
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(filesystem_router, prefix="/api/fs", tags=["filesystem"])
app.include_router(task_router, prefix="/api/task", tags=["task"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])

@app.get("/")
def read_root():
    return {"message": "CodeYun Backend is running"}

if __name__ == "__main__":
    print("Starting backend on 0.0.0.0:8000 (Accessible from other devices)")
    # Run on port 8000, listen on all interfaces
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
