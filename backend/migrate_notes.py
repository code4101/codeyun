from sqlmodel import create_engine, text
from backend.db import DATABASE_URL

engine = create_engine(DATABASE_URL)

def migrate():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE notenode ADD COLUMN position_x FLOAT DEFAULT 0.0"))
            print("Added position_x column")
        except Exception as e:
            print(f"Skipping position_x: {e}")
            
        try:
            conn.execute(text("ALTER TABLE notenode ADD COLUMN position_y FLOAT DEFAULT 0.0"))
            print("Added position_y column")
        except Exception as e:
            print(f"Skipping position_y: {e}")
        
        conn.commit()

if __name__ == "__main__":
    migrate()