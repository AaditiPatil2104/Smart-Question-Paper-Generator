import sqlite3

conn = sqlite3.connect("database.db")
conn.row_factory = sqlite3.Row

cur = conn.cursor()

cur.execute("""
SELECT *
FROM papers
ORDER BY id DESC
LIMIT 1
""")

paper = cur.fetchone()

if paper:
    for key in paper.keys():
        print(f"{key} : {paper[key]}")
else:
    print("No papers found")

conn.close()