from sqlmodel import Session, select
from backend.db import engine
from backend.models import NoteNode
import json

def fix_data():
    with Session(engine) as session:
        nodes = session.exec(select(NoteNode)).all()
        count = 0
        for node in nodes:
            current_fields = node.custom_fields
            
            # Handle empty dict or dict -> list conversion
            if isinstance(current_fields, dict):
                new_list = []
                for k, v in current_fields.items():
                    # Handle legacy dict items
                    # Try to infer type or use default
                    f_type = "string"
                    if isinstance(v, bool): f_type = "boolean"
                    elif isinstance(v, (int, float)): f_type = "number"
                    
                    new_list.append([k, f_type, v])
                
                node.custom_fields = new_list
                session.add(node)
                count += 1
                continue

            # Ensure it's a list
            if isinstance(current_fields, str):
                try:
                    current_fields = json.loads(current_fields)
                except:
                    current_fields = []
            
            if not isinstance(current_fields, list):
                # If it's None or something else, make it empty list
                node.custom_fields = []
                session.add(node)
                count += 1
                continue

            new_list = []
            needs_update = False
            
            for item in current_fields:
                if isinstance(item, dict):
                    # Found a Dict! Convert it.
                    # {"key": "k", "value": "v", "type": "t"} -> ["k", "t", "v"]
                    k = item.get("key", "")
                    v = item.get("value", "")
                    t = item.get("type", "string")
                    new_list.append([k, t, v])
                    needs_update = True
                elif isinstance(item, list):
                    # Already a list
                    new_list.append(item)
            
            if needs_update:
                node.custom_fields = new_list
                session.add(node)
                count += 1
        
        session.commit()
        print(f"Fixed {count} nodes.")

if __name__ == "__main__":
    fix_data()
