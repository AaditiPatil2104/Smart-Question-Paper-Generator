import sqlite3

DB = "database.db"
conn = sqlite3.connect(DB)
cursor = conn.cursor()

# ===================== TEACHERS =====================
cursor.execute("""
CREATE TABLE IF NOT EXISTS teachers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT UNIQUE,
    password TEXT,
    phone TEXT DEFAULT '',
    college TEXT DEFAULT '',
    department TEXT DEFAULT '',
    profile_image TEXT DEFAULT 'default.png',
    dark_mode INTEGER DEFAULT 0,
    created_at TEXT
)
""")

# ===================== HODs =====================
cursor.execute("""
CREATE TABLE IF NOT EXISTS hods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    phone TEXT DEFAULT '',
    college TEXT DEFAULT '',
    department TEXT DEFAULT '',
    created_at TEXT
)
""")

# ===================== PAPERS =====================
cursor.execute("""
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER,
    university TEXT,
    exam_title TEXT,
    qp_code TEXT,
    subject TEXT,
    marks INTEGER,
    exam_date TEXT,
    start_time TEXT,
    end_time TEXT,
    qtype TEXT,
    question_marks TEXT,
    difficulty TEXT,
    instructions TEXT,
    syllabus TEXT,
    questions TEXT,
    created_at TEXT
)
""")

# ===================== QUESTION BANK =====================
cursor.execute("""
CREATE TABLE IF NOT EXISTS question_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER,
    subject TEXT,
    type TEXT,
    question TEXT,
    option_a TEXT,
    option_b TEXT,
    option_c TEXT,
    option_d TEXT,
    answer TEXT,
    marks INTEGER DEFAULT 1,
    difficulty TEXT DEFAULT '',
    chart_type TEXT DEFAULT '',
    chart_data TEXT,
    chart_image TEXT DEFAULT '',
    formula TEXT DEFAULT '',
    is_deleted INTEGER DEFAULT 0
)
""")

# ===================== SAFE MIGRATION FOR OLD DATABASES =====================
def ensure_columns(table, columns):
    existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

ensure_columns("teachers", {
    "phone": "TEXT DEFAULT ''",
    "college": "TEXT DEFAULT ''",
    "department": "TEXT DEFAULT ''",
    "profile_image": "TEXT DEFAULT 'default.png'",
    "dark_mode": "INTEGER DEFAULT 0",
    "created_at": "TEXT"
})

ensure_columns("papers", {
    "university": "TEXT",
    "exam_title": "TEXT",
    "qp_code": "TEXT",
    "question_marks": "TEXT",
    "difficulty": "TEXT",
    "syllabus": "TEXT",
    "instructions": "TEXT"
})

ensure_columns("question_bank", {
    "difficulty": "TEXT DEFAULT ''",
    "chart_type": "TEXT DEFAULT ''",
    "chart_data": "TEXT",
    "chart_image": "TEXT DEFAULT ''",
    "formula": "TEXT DEFAULT ''",
    "is_deleted": "INTEGER DEFAULT 0"
})

# Existing questions from older project versions had no difficulty value.
# Initialize them as Medium so they remain usable by the new difficulty filters.
cursor.execute("UPDATE question_bank SET difficulty='Medium' WHERE LOWER(TRIM(COALESCE(difficulty,''))) IN ('moderate','')")

conn.commit()
conn.close()
print("Database Ready (Schema + Migration Applied)")
