from sqlmodel import Session, select
from backend.db import engine
from backend.models import NoteNode
import json

def inspect_data():
    with Session(engine) as session:
        nodes = session.exec(select(NoteNode)).all()
        for node in nodes:
            print(f"ID: {node.id}, Type: {type(node.custom_fields)}")
            print(f"Value: {node.custom_fields}")

if __name__ == "__main__":
    inspect_data()
