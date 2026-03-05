
import sys
import os
from sqlmodel import Session, select
from sqlalchemy import text

# Add parent directory to path to import backend modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.db import engine, migrate_db
from backend.models import NoteNode

def migrate():
    # Ensure schema is up to date
    migrate_db()
    
    with Session(engine) as session:
        # Fetch all notes
        notes = session.exec(select(NoteNode)).all()
        
        mapping = {
            'project': {'type': 'project', 'status': 'doing'},
            'module': {'type': 'module', 'status': 'doing'},
            'todo': {'type': 'task', 'status': 'todo'},
            'doing': {'type': 'task', 'status': 'doing'},
            'pre-done': {'type': 'task', 'status': 'predone'},
            'done': {'type': 'task', 'status': 'done'},
            'delete': {'type': 'note', 'status': 'delete'},
            'bug': {'type': 'bug', 'status': 'todo'},
            'memo': {'type': 'memo', 'status': 'idea'},
            'note': {'type': 'note', 'status': 'idea'},
            'default': {'type': 'note', 'status': 'idea'},
            None: {'type': 'note', 'status': 'idea'},
            '': {'type': 'note', 'status': 'idea'}
        }
        
        count = 0
        for note in notes:
            old_type = note.node_type
            
            # Map current state
            mapped = mapping.get(old_type, mapping['note']) # Default to note/idea if unknown
            
            # Update fields
            note.node_type = mapped['type']
            note.node_status = mapped['status']
            
            # Update history
            new_history = []
            if note.history:
                for entry in note.history:
                    if entry.get('f') == 'n':
                        old_v = entry.get('v')
                        mapped_v = mapping.get(old_v, mapping['note'])
                        
                        # Add type entry
                        new_history.append({
                            'ts': entry['ts'],
                            'f': 'n',
                            'v': mapped_v['type']
                        })
                        
                        # Add status entry if it's not default 'idea' or if the original type implied a status
                        # Actually, better to just always record the status state at that time?
                        # Or only if it differs from 'idea'?
                        # Let's record it to be safe and explicit.
                        new_history.append({
                            'ts': entry['ts'],
                            'f': 's',
                            'v': mapped_v['status']
                        })
                    else:
                        new_history.append(entry)
            
            note.history = new_history
            session.add(note)
            count += 1
            
        session.commit()
        print(f"Migrated {count} notes.")

if __name__ == "__main__":
    migrate()
