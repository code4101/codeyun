
import pytest
import sys
import os
sys.path.append(os.getcwd())
from sqlmodel import Session, SQLModel, create_engine, select
from backend.models import NoteNode, User

# Use in-memory SQLite for testing
engine = create_engine("sqlite:///:memory:")

def setup_module():
    SQLModel.metadata.create_all(engine)

def test_custom_fields():
    with Session(engine) as session:
        # Create User
        user = User(id=1, username="test", hashed_password="pw")
        session.add(user)
        session.commit()

        # Create Note
        note = NoteNode(
            id="123",
            user_id=1,
            title="Test Note",
            content="Content",
            custom_fields={"priority": "high", "tags": ["a", "b"]}
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        
        assert note.custom_fields["priority"] == "high"
        assert note.custom_fields["tags"] == ["a", "b"]
        
        # Update
        note.custom_fields = {"priority": "low", "new_field": 123}
        session.add(note)
        session.commit()
        session.refresh(note)
        
        assert note.custom_fields["priority"] == "low"
        assert note.custom_fields["new_field"] == 123
        assert "tags" not in note.custom_fields

if __name__ == "__main__":
    setup_module()
    test_custom_fields()
    print("Test passed!")
