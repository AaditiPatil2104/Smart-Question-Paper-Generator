import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

# ===================== ADD NEW COLUMNS SAFELY =====================
columns = [
    ("university", "TEXT"),
    ("exam_title", "TEXT"),
    ("qp_code", "TEXT"),
    ("start_time", "TEXT"),
    ("end_time", "TEXT"),
    ("question_marks", "TEXT"),
    ("difficulty", "TEXT"),
    ("instructions", "TEXT"),
    ("syllabus", "TEXT")
]

for name, dtype in columns:
    try:
        cursor.execute(f"ALTER TABLE papers ADD COLUMN {name} {dtype}")
        print(f"Added: {name}")
    except sqlite3.OperationalError:
        print(f"Exists: {name}")

# ===================== PAPER SECTIONS TABLE =====================
cursor.execute("""
CREATE TABLE IF NOT EXISTS paper_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    section_name TEXT,
    question_type TEXT,
    question_count INTEGER,
    marks_per_question INTEGER,
    attempt_rule TEXT,
    attempt_count INTEGER,
    difficulty TEXT,
    source TEXT,
    FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE
)
""")

conn.commit()
conn.close()

print("Database Updated Successfully")


