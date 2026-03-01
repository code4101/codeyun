import pytest
import sys
import os
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.pool import StaticPool
from datetime import datetime

# Ensure project root is in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.app import app
from backend.db import get_session
from backend.models import User
from backend.core.device import device_manager
from backend.core.auth import get_current_user_from_token

# Use in-memory SQLite for tests
@pytest.fixture(name="engine")
def fixture_engine():
    engine = create_engine(
        "sqlite://", 
        connect_args={"check_same_thread": False}, 
        poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)

@pytest.fixture(name="session")
def fixture_session(engine):
    with Session(engine) as session:
        yield session

@pytest.fixture(name="client")
def fixture_client(session):
    def get_session_override():
        return session
    
    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()

@pytest.fixture(name="test_device")
def fixture_test_device(session):
    # Prepare test data
    device_id = "test-device-local"
    token = "test-token-123"
    
    # Mock config instead of DB
    with patch("backend.core.device.get_local_config", return_value={"api_token": token, "python_exec": "python"}), \
         patch("backend.core.device.get_system_id", return_value=device_id), \
         patch("backend.api.agent.get_system_id", return_value=device_id):
        
        # Reload device manager to pick up the test device from mocked config
        device_manager.devices = {}
        device_manager.load()
        
        yield {"id": device_id, "token": token}
        
        # Cleanup
        device_manager.devices = {}

@pytest.fixture(name="auth_user")
def fixture_auth_user(session, client):
    # Create a test user
    user = User(
        username="testuser", 
        email="test@example.com", 
        hashed_password="pw",
        is_active=True
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    
    # Override dependency
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    
    yield user
    
    # Cleanup is handled by app.dependency_overrides.clear() in client fixture 
    # if we rely on client fixture clearing it. 
    # But since client fixture runs before/after this, we should clear it here too to be safe.
    if get_current_user_from_token in app.dependency_overrides:
        del app.dependency_overrides[get_current_user_from_token]
