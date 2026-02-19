from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from backend.api.task_manager import router as task_router
from backend.api.agent import router as agent_router
from backend.api.filesystem import router as filesystem_router
from backend.api.device import router as device_router
from backend.api.auth import router as auth_router
from backend.core.auth import verify_api_token
from backend.core.device import device_manager
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

@app.on_event("startup")
async def startup_event():
    # Ensure device manager is loaded and local token exists
    # This is redundant if device_manager is instantiated at module level, 
    # but good for explicit initialization order if needed.
    # device_manager.load() is called in __new__, so it's already loaded when imported.
    pass

# Include routers with global authentication
app.include_router(auth_router, prefix="/api/auth", tags=["auth"]) # Public auth
app.include_router(device_router, prefix="/api/devices", tags=["devices"]) # User protected inside
app.include_router(filesystem_router, prefix="/api/fs", tags=["filesystem"], dependencies=[Depends(verify_api_token)])
app.include_router(task_router, prefix="/api/task", tags=["task"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"]) # Remove global dependency, handle inside

@app.get("/")
def read_root():
    return {"message": "CodeYun Backend is running"}

if __name__ == "__main__":
    print("Starting backend on 0.0.0.0:8000 (Accessible from other devices)")
    # Run on port 8000, listen on all interfaces
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
