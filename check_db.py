import sqlite3

conn = sqlite3.connect("database.db")
cur = conn.cursor()

print("PAPERS TABLE:")
cur.execute("PRAGMA table_info(papers);")
for row in cur.fetchall():
    print(row)

conn.close()