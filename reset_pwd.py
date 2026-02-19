from passlib.context import CryptContext
import sqlite3

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# User provided password
hashed_password = pwd_context.hash("gMykPn9bNQLWWMtl")

conn = sqlite3.connect(r'd:\home\chenkunze\slns\codeyun\backend\data\codeyun.db')
cursor = conn.cursor()
cursor.execute("UPDATE user SET hashed_password = ? WHERE username = 'code4101'", (hashed_password,))
conn.commit()
conn.close()
print("Password reset for code4101 to user provided value")