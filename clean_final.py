import sqlite3

conn = sqlite3.connect("database.db")
cur = conn.cursor()

# remove broken question papers
cur.execute("""
DELETE FROM papers
WHERE questions IS NULL
OR questions='[]'
OR questions=''
""")

conn.commit()
conn.close()

print("Cleaned ALL invalid question papers")