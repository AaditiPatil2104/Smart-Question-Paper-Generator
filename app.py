from flask import Flask, render_template, request, redirect, send_file, session, url_for, jsonify
import sqlite3
import json
from reportlab.pdfgen import canvas
from datetime import datetime, date
from google import genai
import os 
from werkzeug.utils import secure_filename
import re
import random
import uuid
import math
import html
import urllib.request
import urllib.error
from pathlib import Path
from ai_agent.pdf_reader import extract_pdf_text
from dotenv import load_dotenv

load_dotenv()

# ================= PAPER FIXER =================

def fix_exam_paper(text):
    """
    Cleans and standardizes generated exam papers.
    """

    # 1. Fix Q numbering spacing issues
    text = re.sub(r"Q(\d+)\.", r"\nQ\1.", text)

    # 2. Fix A/B formatting alignment
    text = re.sub(r"\nA\)", r"\nA)", text)
    text = re.sub(r"\nB\)", r"\nB)", text)

    # 3. Ensure marks formatting
    text = re.sub(r"\[(\d+)\]", r"[\1]", text)

    # 4. Fix broken A/B questions
    text = re.sub(r"A\)\s*(.*?)\nB\)\s*(.*?)\n(?!\[)", r"A) \1\nB) \2", text, flags=re.S)

    # 5. Fix short notes header
    text = re.sub(
        r"Short Notes.*",
        "Short Notes\nAttempt any 3 questions [3 × 4 = 12]",
        text,
        flags=re.S
    )

    # 6. Clean extra blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()

# ================= APP =================
app = Flask(__name__)
app.secret_key = "secret123"

# ================= GEMINI =================
API_KEY = os.getenv("API_KEY", "").strip()

client = None
if API_KEY:
    client = genai.Client(api_key=API_KEY)
else:
    print("⚠️ API_KEY NOT FOUND → local AI / Question Bank fallback will be used")


# ================= LOCAL AI FALLBACK (OLLAMA) =================
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip()

def call_local_model(prompt, timeout=240):
    """Return local Ollama model text, or None when Ollama/model is unavailable."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.25}
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        return str(data.get("response", "")).strip() or None
    except Exception as exc:
        print("LOCAL OLLAMA ERROR:", exc)
        return None


# ================= DB =================
def get_db():
    conn = sqlite3.connect(
        "database.db",
        timeout=30
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_hod_table():
    """Create the Coordinator account table without affecting existing teacher data."""
    conn = get_db()
    conn.execute("""
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
    conn.commit()
    conn.close()


init_hod_table()


def init_question_bank_difficulty():
    """Add difficulty and graph metadata to existing question-bank databases safely."""
    conn = get_db()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(question_bank)").fetchall()}
    wanted = {
        "difficulty": "TEXT DEFAULT ''",
        "chart_type": "TEXT DEFAULT ''",
        "chart_data": "TEXT",
        "chart_image": "TEXT DEFAULT ''",
        "formula": "TEXT DEFAULT ''"
    }
    for name, definition in wanted.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE question_bank ADD COLUMN {name} {definition}")
    conn.execute("UPDATE question_bank SET difficulty='Medium' WHERE LOWER(TRIM(COALESCE(difficulty,''))) IN ('moderate','')")
    conn.commit()
    conn.close()


init_question_bank_difficulty()


def format_time_12h(value):
    """Convert stored 24-hour HH:MM time to a readable 12-hour AM/PM time."""
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%H:%M").strftime("%I:%M %p")
    except (TypeError, ValueError):
        return value


def is_valid_email(value):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (value or "").strip()))


def valid_profile_text(value):
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z .,&()\-]*", (value or "").strip()))


app.jinja_env.filters["time12"] = format_time_12h


# ================= LOGIN CHECK =================
def require_login():
    return "teacher_id" not in session or session.get("role") != "teacher"


def require_hod():
    return "hod_id" not in session or session.get("role") != "hod"


# ================= VALIDATION =================
def validate_form(marks, exam_date):
    if marks <= 0:
        return "Marks must be greater than 0"

    if exam_date <= str(date.today()):
        return "Exam date must be greater than today"

    return None


def valid_letters(value):
    return bool(re.fullmatch(r"[A-Za-z ]+", (value or "").strip()))


def valid_alphanumeric_code(value):
    return bool(re.fullmatch(r"[A-Za-z0-9-]+", (value or "").strip()))


def validate_paper_details(form):
    """Validate one paper-details form field at a time. Returns (field, message)."""
    university = form.get("university", "").strip()
    exam_title = form.get("exam_title", "").strip()
    qp_code = form.get("qp_code", "").strip()
    subject = form.get("subject", "").strip()
    exam_date = form.get("exam_date", "").strip()
    start_time = form.get("start_time", "").strip()
    end_time = form.get("end_time", "").strip()
    marks_text = form.get("marks", "").strip()

    if not valid_letters(university):
        return "university", "University Name must contain only letters and spaces."
    if not valid_letters(exam_title):
        return "exam_title", "Examination Title must contain only letters and spaces."
    if not valid_alphanumeric_code(qp_code):
        return "qp_code", "QP Code may contain only letters, numbers and hyphens."
    if not valid_letters(subject):
        return "subject", "Subject must contain only letters and spaces."

    if not re.fullmatch(r"\d+", marks_text):
        return "marks", "Total Marks must contain numbers only."
    if int(marks_text) <= 0:
        return "marks", "Total Marks must be greater than 0."
    if int(marks_text) > 100:
        return "marks", "Total Marks cannot be greater than 100."

    try:
        selected_date = datetime.strptime(exam_date, "%Y-%m-%d").date()
    except ValueError:
        return "exam_date", "Please select a valid exam date."

    if selected_date <= date.today():
        return "exam_date", "Exam date must be greater than today's date."

    try:
        start = datetime.strptime(start_time, "%H:%M")
        end = datetime.strptime(end_time, "%H:%M")
    except ValueError:
        return "start_time", "Please enter valid start and end times."

    if end <= start:
        return "end_time", "End time must be after start time."

    duration_minutes = int((end - start).total_seconds() // 60)
    if duration_minutes < 180:
        return "end_time", "Exam duration must be at least 3 hours."

    return None, None


def validate_structure(structure):
    if not isinstance(structure, list) or not structure:
        return "Add at least one section to the paper."

    for i, sec in enumerate(structure, 1):
        title = str(sec.get("title", "")).strip()
        if not valid_letters(title):
            return f"Section {i} title must contain only letters and spaces."

        try:
            count = int(sec.get("count", 0))
            marks = int(sec.get("marks", 0))
            attempt = int(sec.get("attempt", 0))
        except (TypeError, ValueError):
            return f"Section {i}: count, marks and attempt must contain numbers only."

        if count <= 0 or marks <= 0 or attempt <= 0:
            return f"Section {i}: count, marks and attempt must be greater than 0."
        if marks > 100:
            return f"Section {i}: marks cannot be greater than 100."
        if attempt > count:
            return f"Section {i}: attempt cannot be greater than the question count."

        sec["difficulty"] = normalize_difficulty(sec.get("difficulty")) or "Medium"
        if sec["difficulty"] not in {"Easy", "Medium", "Hard", "Mixed"}:
            return f"Section {i}: difficulty must be Easy, Medium, Hard or Mixed."

        split = str(sec.get("split", "") or "").strip()
        if split:
            for part in split.split(","):
                if not part.strip().isdigit() or not (1 <= int(part.strip()) <= count):
                    return f"Section {i}: split question numbers must be between 1 and {count}."

    return None


# ================= QUESTION GENERATION HELPERS =================
DIFFICULTIES = {"Easy", "Medium", "Hard", "Mixed"}

def normalize_difficulty(value):
    value = (value or "").strip().lower()
    aliases = {
        "easy": "Easy", "simple": "Easy",
        "medium": "Medium", "moderate": "Medium", "moderate level": "Medium",
        "hard": "Hard", "difficult": "Hard", "difficult level": "Hard",
        "mixed": "Mixed", "mix": "Mixed", "mixed level": "Mixed",
        "": ""
    }
    return aliases.get(value, str(value).strip().title() if value else "")


def normalize_question_type(value):
    value = (value or "").strip().lower()
    if value == "mcq": return "MCQ"
    if value in {"short", "short notes", "short answer", "shortnote"}: return "Short Notes"
    if value in {"long", "long answer", "theory"}: return "Long Answer"
    return value.title() if value else ""



def question_style_rules(difficulty, qtype):
    """Return strict length/style rules so AI output matches the selected level."""
    d = normalize_difficulty(difficulty) or "Medium"
    qt = normalize_question_type(qtype)
    if d == "Easy":
        if qt == "Short Notes":
            return """
EASY SHORT-NOTE RULE: The question MUST be only a short topic/title or very short phrase.
Examples: "Raspberry Pi", "Advantages of IoT", "Sensors", "IoT Gateway".
Do NOT write "Explain...", "Discuss...", "Describe...", "Write a detailed note..." or a sentence.
Target length: 1-6 words.
"""
        if qt == "Long Answer":
            return """
EASY LONG-ANSWER RULE: Use a simple, direct, single-focus question.
Maximum about 100 characters and normally no more than 1-1.5 printed lines.
Examples: "Explain the basic components of IoT.", "Explain the advantages of IoT."
Do NOT combine multiple concepts, domains, scenarios, surveys, case studies or several instructions.
Do NOT create A/B subquestions inside the question text.
"""
        return """
EASY MCQ RULE: Keep the stem short, direct and basic, normally one sentence.
Avoid case studies, long scenarios and multiple concepts.
"""
    if d == "Medium":
        return """
MEDIUM RULE: Use clear, moderately descriptive university questions.
Long Answer questions may be 1-3 sentences, but avoid unnecessary background text.
Short Notes should remain concise topic/concept prompts.
"""
    if d == "Hard":
        return """
HARD RULE: Questions may be analytical, comparative, application-based or multi-step.
For Long Answer, detailed wording is allowed when academically useful.
Short Notes should still be concise topic/concept prompts rather than paragraphs.
"""
    return """
MIXED RULE: Follow the exact style rules for each item's assigned difficulty.
Easy = short/direct, Medium = moderately descriptive, Hard = analytical.
"""

def is_statistics_subject(subject, syllabus=""):
    text = f"{subject} {syllabus}".lower()
    keywords = [
        "statistics", "statistical", "frequency distribution", "histogram",
        "bar graph", "bar chart", "pie chart", "ogive", "frequency polygon",
        "mean", "median", "mode", "standard deviation", "variance", "probability",
        "correlation", "regression", "quartile", "percentile", "sampling"
    ]
    return any(k in text for k in keywords)


def mixed_difficulties(count):
    count = max(0, int(count))
    pattern = ["Easy", "Medium", "Hard"]
    return [pattern[i % 3] for i in range(count)]


def chart_path_for_question(question_id):
    folder = Path(app.static_folder) / "generated_charts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"question_{question_id}_{uuid.uuid4().hex[:8]}.png"


def chart_file_path(chart_image):
    if not chart_image:
        return ""
    p = Path(str(chart_image))
    if p.is_absolute():
        return str(p)
    return str(Path(app.static_folder) / p)


def save_question_upload(file_storage):
    """Save an uploaded question figure and return a static-relative path."""
    if not file_storage or not getattr(file_storage, "filename", ""):
        return ""
    filename = secure_filename(file_storage.filename)
    ext = Path(filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ValueError("Please upload a PNG, JPG, JPEG, WEBP or GIF image.")
    folder = Path(app.static_folder) / "uploads" / "question_images"
    folder.mkdir(parents=True, exist_ok=True)
    saved = f"question_{uuid.uuid4().hex}{ext}"
    file_storage.save(folder / saved)
    return f"uploads/question_images/{saved}"


def create_chart_image(chart_type, chart_data, title="Statistics Graph", xlabel="", ylabel=""):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = chart_data.get("labels", []) or []
        values = chart_data.get("values", []) or []
        bins = chart_data.get("bins", []) or []
        frequencies = chart_data.get("frequencies", []) or values
        if chart_type in {"bar", "bar chart"} and (not labels or not values):
            return None
        if chart_type == "pie" and (not labels or not values):
            return None
        if chart_type in {"histogram", "frequency polygon", "line", "line graph", "ogive"} and not frequencies and not values:
            return None

        fig, ax = plt.subplots(figsize=(7.4, 4.6), dpi=160)
        ctype = chart_type.lower().strip()

        if ctype in {"bar", "bar chart"}:
            ax.bar(labels, values)
        elif ctype == "pie":
            ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")
        elif ctype == "histogram":
            raw = chart_data.get("data", [])
            if raw:
                ax.hist(raw, bins=bins if bins else "auto", edgecolor="black")
            else:
                # Frequency histogram from class intervals / frequencies.
                centers = chart_data.get("centers", [])
                widths = chart_data.get("widths", [])
                if centers and frequencies:
                    if not widths:
                        widths = [1] * len(centers)
                    ax.bar(centers, frequencies, width=widths, align="center", edgecolor="black")
                else:
                    return None
        elif ctype == "frequency polygon":
            x = chart_data.get("x", []) or chart_data.get("centers", []) or labels
            y = frequencies
            ax.plot(x, y, marker="o")
        elif ctype in {"line", "line graph"}:
            x = chart_data.get("x", []) or labels
            ax.plot(x, values if values else frequencies, marker="o")
        elif ctype == "ogive":
            x = chart_data.get("x", []) or labels
            ax.plot(x, frequencies, marker="o")
        else:
            return None

        ax.set_title(title or "Statistics Graph")
        if xlabel: ax.set_xlabel(xlabel)
        if ylabel: ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        temp = Path(app.static_folder) / "generated_charts"
        temp.mkdir(parents=True, exist_ok=True)
        path = temp / f"chart_{uuid.uuid4().hex}.png"
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return str(path.relative_to(Path(app.static_folder))).replace("\\", "/")
    except Exception as e:
        print("CHART ERROR:", e)
        return None


def enrich_graph_question(item, subject="", difficulty="Medium"):
    if not isinstance(item, dict):
        return item
    graph = item.get("graph") or item.get("chart") or {}
    if not isinstance(graph, dict) or not graph.get("type"):
        item["difficulty"] = normalize_difficulty(item.get("difficulty") or difficulty)
        return item
    chart_type = str(graph.get("type", "")).strip().lower()
    try:
        chart_data = graph.get("data") if isinstance(graph.get("data"), dict) else graph
        # Always retain the underlying dataset, but do not render a finished chart
        # when the exam question explicitly asks students to draw/construct/plot it.
        question_text = str(item.get("question", "")).lower()
        asks_student_to_draw = bool(re.search(
            r"\\b(draw|construct|plot|sketch|prepare|draw a|construct a)\\b", question_text
        ))
        item["chart_type"] = chart_type
        item["chart_data"] = chart_data
        item["chart_image"] = ""
        item["formula"] = str(item.get("formula", "") or graph.get("formula", "")).strip()
        if not asks_student_to_draw:
            image_path = create_chart_image(
                chart_type,
                chart_data,
                graph.get("title", "Statistics Graph"),
                graph.get("xlabel", ""),
                graph.get("ylabel", "")
            )
            if image_path:
                item["chart_image"] = image_path
    except Exception as e:
        print("GRAPH ENRICH ERROR:", e)
    item["difficulty"] = normalize_difficulty(item.get("difficulty") or difficulty)
    return item


# ================= AI GENERATION =================
def generate_ai(subject, qtype, count, split=False, difficulty=None, syllabus=""):
    if not client:
        return None

    qtype = normalize_question_type(qtype)
    difficulty = normalize_difficulty(difficulty)
    try:
        if difficulty == "Mixed":
            requested_difficulties = mixed_difficulties(count)
            difficulty_rule = "Use a balanced mix of Easy, Medium and Hard questions. Assign each item a difficulty field."
        else:
            requested_difficulties = [difficulty or "Medium"] * count
            difficulty_rule = f"All questions must be {difficulty or 'Medium'} level."

        stats_rule = ""
        if is_statistics_subject(subject, syllabus):
            stats_rule = r"""
STATISTICS / GRAPH RULES:
- Make at least half of the generated questions graph/data based when the syllabus covers data presentation or descriptive statistics; rotate between bar, pie, histogram, line, frequency polygon and ogive where appropriate.
- Every graph-based question MUST include a graph object with type, title, axis labels where relevant, and complete numeric data sufficient to render a real image.
- IMPORTANT: For questions that ask students to DRAW/CONSTRUCT/PLOT a chart, DO NOT show or embed the finished chart image. Provide the source data only and leave the student to draw it.
- For every construct/draw question, include the dataset in the question text as a clear columnar Markdown-style table (for example: | Class interval | Frequency | on separate rows). Never write all data as a comma-separated sentence.
- For pie-chart construction, provide category and frequency/value columns and ask students to calculate sector angles and draw the pie chart; do not provide the completed pie chart.
- For histogram construction, provide class intervals and frequencies in two columns. For frequency polygon/ogive, provide class intervals, frequencies and cumulative frequencies as appropriate.
- Only include a rendered chart image when the question explicitly asks students to READ/INTERPRET an already supplied chart. Never include both a request to draw a chart and the finished chart.
- For pie charts provide labels and positive values; for histograms provide raw numeric data or class centers, widths and frequencies; for line graphs provide x and values; for bar charts provide labels and values.
- Use realistic, internally consistent labels and frequencies. Never invent data that contradicts the syllabus topic.
- Include mathematical notation/formulas when appropriate (for example x̄, Σx, σ², μ, N, f, cf, P(A), r).
- For non-graph questions, formulas/symbols may still be used when appropriate.
"""

        if qtype == "MCQ":
            type_rules = """Each item must contain: question, options (array of exactly 4 strings), answer, answer_key, marks, difficulty, graph.
For non-graph questions use graph as an empty object. Use marks=1."""
        elif qtype == "Short Notes":
            type_rules = """Each item must contain: question, options (empty array), answer, answer_key, marks, difficulty, graph.
Use marks=4. For a graph item, the question should ask the student to interpret, construct, identify or calculate from the supplied graph."""
        else:
            type_rules = """Each item must contain: question, options (empty array), answer, answer_key, marks, difficulty, graph.
Use marks=12. Graph questions should be suitable for a university long-answer paper and may require drawing, interpretation, calculation or comparison."""

        split_instruction = ""
        if split:
            split_instruction = "Generate exactly two related subquestions labelled A and B."

        prompt = f"""
You are an expert university examination question setter.
Subject: {subject}
Question type: {qtype}
Number of questions: {count}
Difficulty instruction: {difficulty_rule}
{split_instruction}
{question_style_rules(difficulty, qtype)}

Question quality requirements:
- Short-answer questions must be focused, answerable in 2–5 sentences or a small calculation, and must test a specific syllabus concept.
- Avoid vague prompts such as "Explain statistics", duplicate questions, incomplete wording, and questions whose answer cannot be determined from the question/data.
- For numerical questions, include all required values and units, and ensure data are internally consistent.
- The answer field must contain a correct concise model answer or calculation; for MCQs the answer and answer_key must agree with exactly one option.
- Long Answer questions worth 8–12 marks should normally contain a coherent main question followed by 2–3 labelled subparts (a), (b), (c), each with a distinct task and marks that add to the question total. Do not make a question a single vague paragraph.
- Short Notes must be meaningful, exam-ready prompts about a specific syllabus concept (e.g. "Write a short note on measures of dispersion and explain two uses"). Never use labels like "Highest Sales Month" unless the required dataset/figure and a clear task are included. Do not duplicate an earlier question or repeat the same question as a comparison.
- When all sections are selected, create distinct questions suitable for each section: MCQs have four options; short notes are concise concept prompts; long answers have a structured task and subparts when appropriate.
- For table-based datasets, include a clear heading row and a separate row for each observation. Keep the dataset separate from the question wording.

SYLLABUS:
----------------
{syllabus[:30000]}
----------------
{stats_rule}
{type_rules}

General rules:
1. Generate exactly {count} independent questions unless split mode is explicitly requested.
2. Generate only from the syllabus. If the syllabus is incomplete, use only the subject concepts clearly implied by it.
3. Never add markdown or text outside the JSON.
4. Return ONLY a valid JSON array.
5. Keep every graph dataset small enough to fit clearly on an exam page.
6. For graph objects use this structure when needed:
   {{"type":"bar|pie|histogram|line|frequency polygon|ogive","title":"...","xlabel":"...","ylabel":"...","data":{{...}}}}
7. For histogram, prefer either data:[raw numeric observations] or centers:[...], frequencies:[...], widths:[...].
8. For a pie chart use labels:[...], values:[...].
9. For bar/line/frequency polygon use labels/x plus values/frequencies as appropriate.

JSON format example:
[
  {{
    "question": "A school survey gives the following data. Calculate the percentage represented by each category.",
    "options": [],
    "answer": "...",
    "answer_key": "",
    "marks": 4,
    "difficulty": "Easy",
    "graph": {{"type":"pie","title":"Student Preference","data":{{"labels":["A","B","C"],"values":[30,45,25]}}}}
  }}
]
"""

        ai_text = None
        if client:
            try:
                response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                ai_text = getattr(response, "text", None)
            except Exception as gemini_error:
                print("GEMINI GENERATION ERROR; trying local Ollama:", gemini_error)
        if not ai_text:
            ai_text = call_local_model(prompt)
        data = clean_json_response(ai_text)
        if not isinstance(data, list):
            return None

        cleaned = []
        for idx, item in enumerate(data[:count]):
            if not isinstance(item, dict) or not str(item.get("question", "")).strip():
                continue
            opts = item.get("options") or []
            if qtype == "MCQ" and len(opts) != 4:
                continue
            d = normalize_difficulty(item.get("difficulty") or requested_difficulties[min(idx, len(requested_difficulties)-1)])
            item["question"] = str(item.get("question", "")).strip()
            item["options"] = [str(x).strip() for x in opts]
            item["answer"] = str(item.get("answer", "")).strip()
            item["answer_key"] = str(item.get("answer_key", "")).strip()
            item["marks"] = int(item.get("marks", 1 if qtype == "MCQ" else (4 if qtype == "Short Notes" else 12)))
            item["difficulty"] = d
            item = enrich_graph_question(item, subject, d)
            cleaned.append(item)

        return cleaned if len(cleaned) == count else None
    except Exception as e:
        print("AI ERROR:", e)
        return None


def generate_mcqs_from_syllabus(syllabus_text, count):

    if not client:
        return None

    prompt = f"""
You are an expert university question paper setter.

Generate exactly {count} Multiple Choice Questions ONLY from the syllabus below.

SYLLABUS:
-----------------------
{syllabus_text}
-----------------------

Rules:

1. Generate exactly {count} MCQs.
2. Every question must come only from the syllabus.
3. Each question must have four options.
4. Give the correct answer.
5. Return ONLY in this format.

Question: What is IoT?
A) Internet of Things
B) Internet of Technology
C) Intelligent Object Technology
D) None of these
Answer: A

Question: ........
A) .....
B) .....
C) .....
D) .....
Answer: C

Do not write explanations.
Do not use markdown.
"""

    try:

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        return response.text

    except Exception as e:
        print(e)
        return None
# ================= QUESTION BANK GROUPING =================
def group_questions(rows):
    grouped = {}
    for r in rows:
        subject = (r["subject"] or "").strip()
        if not subject or not (r["question"] or "").strip():
            continue
        qtype = (r["type"] or "").strip().upper()
        if subject not in grouped:
            grouped[subject] = {"MCQ": [], "Short Notes": [], "Long Answer": []}
        q = {
            "id": r["id"], "question": r["question"], "answer": r["answer"],
            "a": r["option_a"], "b": r["option_b"], "c": r["option_c"], "d": r["option_d"],
            "marks": r["marks"], "difficulty": normalize_difficulty(r["difficulty"]),
            "chart_type": r["chart_type"] if "chart_type" in r.keys() else "",
            "chart_image": r["chart_image"] if "chart_image" in r.keys() else "",
        }
        if qtype == "MCQ": grouped[subject]["MCQ"].append(q)
        elif qtype in ["SHORT", "SHORT NOTES", "SHORT ANSWER"]: grouped[subject]["Short Notes"].append(q)
        elif qtype in ["LONG", "LONG ANSWER", "THEORY"]: grouped[subject]["Long Answer"].append(q)
    return grouped


# ================= HOME =================
@app.route("/")
def home():
    return render_template("home.html")


# ================= REGISTER =================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        if not valid_letters(name):
            return render_template("register.html", error="Full Name must contain only letters and spaces.")
        if not re.fullmatch(r"[6-9]\d{9}", phone):
            return render_template("register.html", error="Contact Number must be exactly 10 digits and start with 6, 7, 8 or 9.")
        if not is_valid_email(email):
            return render_template("register.html", error="Please enter a valid email address.")
        if not (6 <= len(password) <= 10):
            return render_template("register.html", error="Password must be between 6 and 10 characters.")

        conn = get_db()
        cur = conn.cursor()
        try:
            joined_date = datetime.now().strftime("%d %B %Y")
            cur.execute("""
                INSERT INTO teachers
                (name, email, password, phone, college, department, profile_image, created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (name, email, password, phone, "", "", "default.png", joined_date))
            conn.commit()
            session.clear()
            session["teacher_id"] = cur.lastrowid
            session["teacher_name"] = name
            session["role"] = "teacher"
            conn.close()
            return redirect("/dashboard")
        except sqlite3.IntegrityError:
            conn.close()
            return render_template("register.html", error="Email already exists.")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM teachers WHERE email=? AND password=?", (email, password)).fetchone()
        conn.close()
        if user:
            session.clear()
            session["teacher_id"] = user["id"]
            session["teacher_name"] = user["name"]
            session["role"] = "teacher"
            return redirect("/dashboard")
        return render_template("login.html", error="Invalid email or password.")
    return render_template("login.html", success="Password reset successfully. Please login with your new password.") if request.args.get("reset") == "1" else render_template("login.html")


@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not is_valid_email(email):
            return render_template("forgot_password.html", error="Please enter a valid email address.")
        if not (6 <= len(new_password) <= 10):
            return render_template("forgot_password.html", error="Password must be between 6 and 10 characters.")
        if new_password != confirm_password:
            return render_template("forgot_password.html", error="Passwords do not match.")

        conn = get_db()
        # Search both account types so the same reset page works for Teacher and Coordinator.
        teacher = conn.execute("SELECT id FROM teachers WHERE email=?", (email,)).fetchone()
        hod = conn.execute("SELECT id FROM hods WHERE email=?", (email,)).fetchone()
        if teacher:
            conn.execute("UPDATE teachers SET password=? WHERE id=?", (new_password, teacher["id"]))
        elif hod:
            conn.execute("UPDATE hods SET password=? WHERE id=?", (new_password, hod["id"]))
        else:
            conn.close()
            return render_template("forgot_password.html", error="No account found with this email address.")
        conn.commit()
        conn.close()
        # Return the user directly to the correct login form after a successful reset.
        if teacher:
            return redirect(url_for("login", reset="1"))
        return redirect(url_for("hod_login", reset="1"))

    return render_template("forgot_password.html")


# ================= Coordinator AUTHENTICATION =================
@app.route("/hod/register", methods=["GET", "POST"])
def hod_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        college = request.form.get("college", "").strip()
        department = request.form.get("department", "").strip()

        if not valid_letters(name):
            return render_template("hod_register.html", error="Full Name must contain only letters and spaces.")
        if not re.fullmatch(r"[6-9]\d{9}", phone):
            return render_template("hod_register.html", error="Contact Number must be exactly 10 digits and start with 6, 7, 8 or 9.")
        if not is_valid_email(email):
            return render_template("hod_register.html", error="Please enter a valid email address.")
        if not (6 <= len(password) <= 10):
            return render_template("hod_register.html", error="Password must be between 6 and 10 characters.")
        if college and not valid_profile_text(college):
            return render_template("hod_register.html", error="College must contain valid characters only.")
        if department and not valid_profile_text(department):
            return render_template("hod_register.html", error="Department must contain valid characters only.")

        conn = get_db()
        try:
            if conn.execute("SELECT 1 FROM teachers WHERE email=?", (email,)).fetchone() or conn.execute("SELECT 1 FROM hods WHERE email=?", (email,)).fetchone():
                return render_template("hod_register.html", error="Email already exists.")
            cur = conn.execute("""
                INSERT INTO hods(name,email,password,phone,college,department,created_at)
                VALUES(?,?,?,?,?,?,?)
            """, (name, email, password, phone, college, department, datetime.now().strftime("%d %B %Y")))
            conn.commit()
            session.clear()
            session["hod_id"] = cur.lastrowid
            session["hod_name"] = name
            session["role"] = "hod"
            return redirect("/hod/dashboard")
        finally:
            conn.close()

    return render_template("hod_register.html")


@app.route("/hod/login", methods=["GET", "POST"])
def hod_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not is_valid_email(email):
            return render_template("hod_login.html", error="Please enter a valid email address.")
        conn = get_db()
        user = conn.execute("SELECT * FROM hods WHERE email=? AND password=?", (email, password)).fetchone()
        conn.close()
        if user:
            session.clear()
            session["hod_id"] = user["id"]
            session["hod_name"] = user["name"]
            session["role"] = "hod"
            return redirect("/hod/dashboard")
        return render_template("hod_login.html", error="Invalid email or password.")
    return render_template("hod_login.html", success="Password reset successfully. Please login with your new password.") if request.args.get("reset") == "1" else render_template("hod_login.html")


def hod_required_redirect():
    return redirect("/hod/login") if require_hod() else None


# ================= DASHBOARD =================
@app.route("/dashboard")
def dashboard():

    if require_login():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    teacher_id = session["teacher_id"]

    # ==========================
    # Total Papers Generated
    # ==========================
    cur.execute("""
        SELECT COUNT(*)
        FROM papers
        WHERE teacher_id=?
    """, (teacher_id,))
    total = cur.fetchone()[0]

    # ==========================
    # Total Questions
    # ==========================
    cur.execute("""
        SELECT COUNT(*)
        FROM question_bank
        WHERE teacher_id=?
    """, (teacher_id,))
    bank_total = cur.fetchone()[0]

    # ==========================
    # MCQ Questions
    # ==========================
    cur.execute("""
        SELECT COUNT(*)
        FROM question_bank
        WHERE teacher_id=?
        AND type='MCQ'
    """, (teacher_id,))
    mcq_total = cur.fetchone()[0]

    # ==========================
    # Short Questions
    # Supports both old and new names
    # ==========================
    cur.execute("""
        SELECT COUNT(*)
        FROM question_bank
        WHERE teacher_id=?
        AND type IN ('SHORT','Short Note','Short Notes')
    """, (teacher_id,))
    short_total = cur.fetchone()[0]

    # ==========================
    # Long Questions
    # Supports both old and new names
    # ==========================
    cur.execute("""
        SELECT COUNT(*)
        FROM question_bank
        WHERE teacher_id=?
        AND type IN ('LONG','Long Answer')
    """, (teacher_id,))
    long_total = cur.fetchone()[0]

    conn.close()

    return render_template(
        "dashboard.html",
        total=total,
        bank_total=bank_total,
        mcq_total=mcq_total,
        short_total=short_total,
        long_total=long_total
    )
# ================= Coordinator MODULE =================
@app.route("/hod/dashboard")
def hod_dashboard():
    if require_hod():
        return redirect("/hod/login")
    conn = get_db()
    teachers = conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0]
    papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    questions = conn.execute("SELECT COUNT(*) FROM question_bank WHERE COALESCE(is_deleted,0)=0").fetchone()[0]
    conn.close()
    return render_template("hod_dashboard.html", teachers=teachers, papers=papers, questions=questions)


@app.route("/hod/teachers")
def hod_teachers():
    if require_hod():
        return redirect("/hod/login")
    conn = get_db()
    rows = conn.execute("SELECT id,name,email,phone,college,department,created_at FROM teachers ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("hod_teachers.html", teachers=rows)


@app.route("/hod/papers")
def hod_papers():
    if require_hod():
        return redirect("/hod/login")
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*, t.name AS teacher_name, t.email AS teacher_email
        FROM papers p
        LEFT JOIN teachers t ON t.id=p.teacher_id
        ORDER BY p.id DESC
    """).fetchall()
    conn.close()
    return render_template("hod_papers.html", papers=rows)


@app.route("/hod/view_paper/<int:paper_id>")
def hod_view_paper(paper_id):
    if require_hod():
        return redirect("/hod/login")
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    conn.close()
    if not paper:
        return "Paper not found", 404
    paper = dict(paper)
    try:
        paper["exam_date"] = datetime.strptime(paper["exam_date"], "%Y-%m-%d").strftime("%d %B %Y")
    except (TypeError, ValueError):
        pass
    try:
        questions = json.loads(paper["questions"] or "[]")
    except (TypeError, ValueError):
        questions = []
    for sec in questions:
        if isinstance(sec, dict) and "section" in sec and "title" not in sec:
            sec["title"] = sec["section"]
    sections = questions if questions and isinstance(questions[0], dict) and "title" in questions[0] else None
    return render_template("view_paper.html", paper=paper, questions=questions, sections=sections, show_section=bool(sections and len(sections)>1), hod_view=True)


@app.route("/hod/question_bank")
def hod_question_bank():
    if require_hod():
        return redirect("/hod/login")
    conn = get_db()
    rows = conn.execute("""
        SELECT qb.*, t.name AS teacher_name, t.email AS teacher_email
        FROM question_bank qb
        LEFT JOIN teachers t ON t.id=qb.teacher_id
        WHERE COALESCE(qb.is_deleted,0)=0
          AND TRIM(COALESCE(qb.question,'')) <> ''
        ORDER BY qb.id DESC
    """).fetchall()
    conn.close()
    return render_template("hod_question_bank.html", questions=rows)


@app.route("/hod/reports")
def hod_reports():
    if require_hod():
        return redirect("/hod/login")
    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()
    subject = request.args.get("subject", "").strip()
    teacher_name = request.args.get("teacher_name", "").strip()

    conn = get_db()
    where = ["1=1"]
    params = []
    if from_date:
        where.append("p.exam_date >= ?")
        params.append(from_date)
    if to_date:
        where.append("p.exam_date <= ?")
        params.append(to_date)
    if subject:
        where.append("LOWER(p.subject) LIKE LOWER(?)")
        params.append(f"%{subject}%")
    if teacher_name:
        where.append("LOWER(t.name) LIKE LOWER(?)")
        params.append(f"%{teacher_name}%")

    query = f"""
        SELECT p.id,p.university,p.exam_title,p.qp_code,p.subject,p.marks,p.exam_date,
               p.start_time,p.end_time,p.created_at,t.name AS teacher_name,t.email AS teacher_email
        FROM papers p
        LEFT JOIN teachers t ON t.id=p.teacher_id
        WHERE {' AND '.join(where)}
        ORDER BY p.exam_date DESC, p.id DESC
    """
    rows = conn.execute(query, params).fetchall()
    subjects = [r[0] for r in conn.execute("SELECT DISTINCT subject FROM papers WHERE TRIM(COALESCE(subject,''))<>'' ORDER BY subject").fetchall()]
    teachers = [r[0] for r in conn.execute("SELECT DISTINCT name FROM teachers WHERE TRIM(COALESCE(name,''))<>'' ORDER BY name").fetchall()]
    total_marks = sum(int(r["marks"] or 0) for r in rows)
    conn.close()

    return render_template(
        "hod_reports.html",
        papers=rows,
        subjects=subjects,
        teachers=teachers,
        from_date=from_date,
        to_date=to_date,
        subject=subject,
        teacher_name=teacher_name,
        total_papers=len(rows),
        total_marks=total_marks
    )


def _build_report_pdf(rows, from_date, to_date, subject, teacher_name):
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12*mm, rightMargin=12*mm,
                            topMargin=12*mm, bottomMargin=12*mm, title="Question Paper Report")
    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8, leading=10)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")

    def P(v, st=cell):
        return Paragraph(html.escape("" if v is None else str(v)), st)

    total_marks = 0
    for r in rows:
        try:
            total_marks += int(r["marks"] or 0)
        except (TypeError, ValueError):
            pass

    filters = []
    if from_date: filters.append(f"From: {from_date}")
    if to_date: filters.append(f"To: {to_date}")
    if subject: filters.append(f"Subject: {subject}")
    if teacher_name: filters.append(f"Teacher: {teacher_name}")

    story = [
        Paragraph("Question Paper Report", styles["Title"]),
        Paragraph("Filters: " + (", ".join(filters) if filters else "None (all papers)"), styles["Normal"]),
        Paragraph(f"Generated on: {datetime.now().strftime('%d-%m-%Y %I:%M %p')} &nbsp;|&nbsp; "
                  f"Total Papers: {len(rows)} &nbsp;|&nbsp; Total Marks: {total_marks}", styles["Normal"]),
        Spacer(1, 6*mm),
    ]

    headers = ["Paper ID", "Exam Date", "Subject", "Exam Title", "QP Code", "Marks", "Start", "End", "Teacher", "Teacher Email"]
    data = [[P(h, head) for h in headers]]
    for r in rows:
        data.append([P(r["id"]), P(r["exam_date"]), P(r["subject"]), P(r["exam_title"]), P(r["qp_code"]),
                     P(r["marks"]), P(format_time_12h(r["start_time"])), P(format_time_12h(r["end_time"])),
                     P(r["teacher_name"]), P(r["teacher_email"])])
    widths = [16, 24, 36, 40, 24, 14, 20, 20, 33, 50]
    scale = (doc.width) / (sum(widths) * mm)
    table = Table(data, colWidths=[w*mm*scale for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5fa")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c2d1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)

    def footer(c, d):
        c.saveState(); c.setFont("Helvetica", 8)
        c.drawRightString(landscape(A4)[0] - 12*mm, 7*mm, f"Page {d.page}")
        c.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name="question_paper_report.pdf")


@app.route("/hod/reports/export")
def hod_report_export():
    if require_hod():
        return redirect("/hod/login")
    import csv
    from io import StringIO

    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()
    subject = request.args.get("subject", "").strip()
    teacher_name = request.args.get("teacher_name", "").strip()
    conn = get_db()
    where = ["1=1"]
    params = []
    if from_date:
        where.append("p.exam_date >= ?"); params.append(from_date)
    if to_date:
        where.append("p.exam_date <= ?"); params.append(to_date)
    if subject:
        where.append("LOWER(p.subject) LIKE LOWER(?)"); params.append(f"%{subject}%")
    if teacher_name:
        where.append("LOWER(t.name) LIKE LOWER(?)"); params.append(f"%{teacher_name}%")
    rows = conn.execute(f"""
        SELECT p.id,p.exam_date,p.subject,p.exam_title,p.qp_code,p.marks,p.start_time,p.end_time,t.name AS teacher_name,t.email AS teacher_email
        FROM papers p LEFT JOIN teachers t ON t.id=p.teacher_id
        WHERE {' AND '.join(where)} ORDER BY p.exam_date DESC,p.id DESC
    """, params).fetchall()
    conn.close()
    if request.args.get("format", "csv").lower() == "pdf":
        return _build_report_pdf(rows, from_date, to_date, subject, teacher_name)
    output=StringIO(); writer=csv.writer(output)
    writer.writerow(["Paper ID","Exam Date","Subject","Exam Title","QP Code","Total Marks","Start Time","End Time","Teacher","Teacher Email"])
    for r in rows:
        writer.writerow([r["id"],r["exam_date"],r["subject"],r["exam_title"],r["qp_code"],r["marks"],format_time_12h(r["start_time"]),format_time_12h(r["end_time"]),r["teacher_name"],r["teacher_email"]])
    from flask import Response
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=question_paper_report.csv"})


@app.route("/hod/logout")
def hod_logout():
    session.clear()
    return redirect("/")


# ================= PAPER DETAILS =================
@app.route("/paper_details")
def paper_details():
    if require_login():
        return redirect("/login")

    return render_template("paper_details.html")


# ================= PAPER STRUCTURE =================
@app.route("/paper_structure", methods=["POST"])
def paper_structure():
    if require_login():
        return redirect("/login")

    error_field, error = validate_paper_details(request.form)
    if error:
        return render_template("paper_details.html", error=error, error_field=error_field, form_data=request.form), 400

    session["paper_details"] = request.form.to_dict(flat=False)
    return render_template("paper_structure.html")


# ================= PREVIEW PAPER =================
@app.route("/preview_paper", methods=["POST"])
def preview_paper():
    data = json.loads(request.form["structure"])
    session["paper_structure"] = data
    return render_template("preview.html", data=data)

def generate_b_question(text, difficulty="Medium"):
    """Create a concise companion B question; never turn Easy questions into essays."""
    difficulty = normalize_difficulty(difficulty) or "Medium"
    base = text.replace("?", "").strip()
    if difficulty == "Easy":
        patterns = [
            f"List two advantages of {base}.",
            f"Give two examples of {base}.",
            f"State two uses of {base}.",
            f"Name two features of {base}."
        ]
    elif difficulty == "Medium":
        patterns = [
            f"Explain two applications of {base}.",
            f"Describe two advantages of {base}.",
            f"Explain the importance of {base}."
        ]
    else:
        patterns = [
            f"Analyze the advantages and limitations of {base}.",
            f"Discuss the applications and challenges of {base}.",
            f"Compare {base} with a related concept."
        ]
    return random.choice(patterns)

# ================= GENERATE PAPER FROM STRUCTURE =================
# ================= GENERATE PAPER FROM STRUCTURE =================
@app.route("/generate_paper_from_structure", methods=["POST"])
def generate_paper_from_structure():

    if require_login():
        return redirect("/login")

    structure = session.get("paper_structure", [])

    structure_error = validate_structure(structure)
    if structure_error:
        return structure_error, 400

    # ================= PAPER DETAILS =================

    details = session.get("paper_details", {})

    university = details.get("university", [""])[0]
    exam_title = details.get("exam_title", [""])[0]
    qp_code = details.get("qp_code", [""])[0]
    subject = details.get("subject", [""])[0]

    detail_error_field, detail_error = validate_paper_details({k: (v[0] if isinstance(v, list) and v else "") for k, v in details.items()})
    if detail_error:
        return detail_error, 400

    marks = int(details.get("marks", [0])[0])

    exam_date = details.get("exam_date", [""])[0]
    start_time = details.get("start_time", [""])[0]
    end_time = details.get("end_time", [""])[0]

    instructions = details.get("instructions", [""])[0]

    final_questions = []
    calculated_total_marks = 0

    conn = get_db()
    cur = conn.cursor()

    # ================= SECTION LOOP =================

    for sec in structure:

        title = sec.get("title", "")
        qtype = sec.get("type", "")
        source = sec.get("source", "AI")

        count = int(sec.get("count", 0))
        attempt = int(sec.get("attempt", 0))
        question_mark = int(sec.get("marks", 1))
        calculated_total_marks += attempt * question_mark

        # ================= SPLIT QUESTIONS =================

        split_set = set()

        if sec.get("split"):
            try:
                split_set = {
                    int(x.strip())
                    for x in sec["split"].split(",")
                    if x.strip().isdigit()
                }
            except:
                split_set = set()

        questions = []

        # ================= AI =================

        if source == "AI":

            # AI providers may return None when a quota is exhausted or a
            # local model is unavailable. Normalize that result to a list so
            # fallback Question Bank handling can safely append to it.
            generated_questions = generate_ai(
                subject,
                qtype,
                count,
                difficulty=sec.get("difficulty"),
                syllabus=details.get("syllabus", [""])[0] if details.get("syllabus") else ""
            )
            questions = generated_questions if isinstance(generated_questions, list) else []
            questions = [q for q in questions if isinstance(q, dict) and str(q.get("question", "")).strip()]

            if questions:
                save_ai_questions(
                    session["teacher_id"], subject, qtype, questions
                )
            if not questions:
                # If both Gemini and local Ollama are unavailable/quota-limited,
                # continue with suitable saved questions instead of creating a blank section.
                fallback_types = {
                    "MCQ": ["MCQ"],
                    "Short Notes": ["SHORT", "SHORT NOTES", "Short Notes", "Short Note"],
                    "Long Answer": ["LONG", "LONG ANSWER", "Long Answer"]
                }.get(qtype, [qtype])
                marks_sql = ",".join("?" for _ in fallback_types)
                cur.execute(
                    "SELECT question, option_a, option_b, option_c, option_d, type, difficulty, chart_type, chart_data, chart_image, formula "
                    "FROM question_bank WHERE teacher_id=? AND LOWER(TRIM(subject))=LOWER(TRIM(?)) "
                    f"AND type IN ({marks_sql}) AND COALESCE(is_deleted,0)=0 "
                    "AND TRIM(COALESCE(question,''))<>'' ORDER BY RANDOM() LIMIT ?",
                    (session["teacher_id"], subject, *fallback_types, count)
                )
                fallback_rows = cur.fetchall()
                for r in fallback_rows:
                    item = {
                        "question": r["question"],
                        "type": qtype,
                        "marks": question_mark,
                        "difficulty": normalize_difficulty(r["difficulty"]) or "Unclassified"
                    }
                    if qtype == "MCQ":
                        item["options"] = [r["option_a"], r["option_b"], r["option_c"], r["option_d"]]
                    if r["chart_type"]:
                        item["chart_type"] = r["chart_type"]
                        try:
                            item["chart_data"] = json.loads(r["chart_data"]) if r["chart_data"] else {}
                        except Exception:
                            item["chart_data"] = {}
                        item["chart_image"] = r["chart_image"] or ""
                    questions.append(item)
                if len(questions) < count:
                    return (f"Not enough questions to complete the {title} section. Gemini may be rate-limited and local Ollama may be unavailable. "
                            f"Requested {count}; generated or found {len(questions)}. Start Ollama or add more questions to the Question Bank."), 400
        # ====================================================
        # QUESTION BANK
        # ====================================================

        else:

            type_map = {
                "MCQ": ["MCQ"],
                "Short Notes": ["SHORT", "SHORT NOTES", "Short Notes", "Short Note"],
                "Long Answer": ["LONG", "LONG ANSWER", "Long Answer"]
            }
            allowed_types = type_map.get(qtype, [qtype])
            placeholders = ",".join("?" for _ in allowed_types)

            selected_diff = normalize_difficulty(sec.get("difficulty"))
            common_where = f"""
                FROM question_bank
                WHERE teacher_id=? AND subject=? AND type IN ({placeholders})
                  AND COALESCE(is_deleted, 0)=0
                  AND TRIM(COALESCE(question, '')) <> ''
            """
            if selected_diff == "Mixed":
                # Guarantee a visible mixture whenever enough questions of each level exist.
                targets = mixed_difficulties(count)
                rows = []
                used_ids = set()
                for wanted_diff in targets:
                    sql = "SELECT question, option_a, option_b, option_c, option_d, type, difficulty, chart_type, chart_data, chart_image, formula, id " + common_where
                    excluded = list(used_ids)
                    extra = (" AND id NOT IN (" + ",".join("?" for _ in excluded) + ")") if excluded else ""
                    sql += " AND LOWER(TRIM(COALESCE(difficulty,''))) = LOWER(?)" + extra + " ORDER BY RANDOM() LIMIT 1"
                    params = [session["teacher_id"], subject, *allowed_types, wanted_diff, *excluded]
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
                    if row:
                        rows.append(row); used_ids.add(row["id"])
                if len(rows) < count:
                    sql = "SELECT question, option_a, option_b, option_c, option_d, type, difficulty, chart_type, chart_data, chart_image, formula, id " + common_where
                    excluded = list(used_ids)
                    extra = (" AND id NOT IN (" + ",".join("?" for _ in excluded) + ")") if excluded else ""
                    cur.execute(sql + extra + " ORDER BY RANDOM() LIMIT ?", (session["teacher_id"], subject, *allowed_types, *excluded, count-len(rows)))
                    rows.extend(cur.fetchall())
                random.shuffle(rows)
            else:
                sql = "SELECT question, option_a, option_b, option_c, option_d, type, difficulty, chart_type, chart_data, chart_image, formula " + common_where
                params = [session["teacher_id"], subject, *allowed_types]
                if selected_diff in {"Easy", "Medium", "Hard"}:
                    sql += " AND LOWER(TRIM(COALESCE(difficulty,''))) = LOWER(?) "
                    params.append(selected_diff)
                sql += " ORDER BY RANDOM() LIMIT ?"
                params.append(count)
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()

            if len(rows) < count:
                wanted = str(sec.get("difficulty", "")).strip() or "the selected"
                return (f"Not enough {wanted} {qtype} questions are available in the Question Bank for {subject}. "
                        f"Requested {count}, but only {len(rows)} are available. Please add/classify more questions."), 400

            for r in rows:

                if r["type"] == "MCQ":

                    q = {
                        "question": r["question"],
                        "options": [r["option_a"], r["option_b"], r["option_c"], r["option_d"]],
                        "type": "MCQ",
                        "difficulty": normalize_difficulty(r["difficulty"]) or "Unclassified"
                    }
                    if "chart_type" in r.keys() and r["chart_type"]:
                        q["chart_type"] = r["chart_type"]
                        q["chart_data"] = json.loads(r["chart_data"]) if r["chart_data"] else {}
                        q["chart_image"] = r["chart_image"] or ""
                    questions.append(q)

                else:

                    q = {
                        "question": r["question"],
                        "type": r["type"],
                        "difficulty": normalize_difficulty(r["difficulty"]) or "Unclassified"
                    }
                    if "chart_type" in r.keys() and r["chart_type"]:
                        q["chart_type"] = r["chart_type"]
                        q["chart_data"] = json.loads(r["chart_data"]) if r["chart_data"] else {}
                        q["chart_image"] = r["chart_image"] or ""
                    questions.append(q)

        # ====================================================
        # PROCESS QUESTIONS
        # ====================================================

        processed_questions = []

        for index, q in enumerate(questions):

            question_number = index + 1

            # Ensure every question has marks
            if isinstance(q, dict):
                q["marks"] = question_mark
                            # ---------- Split Question ----------
            if question_number in split_set:

                text = q.get("question", "") if isinstance(q, dict) else str(q)

                processed_questions.append({

                    "question": "",
                    "type": qtype,
                    "marks": question_mark,

                    "subparts": [

                        {
                            "label": "A",
                            "question": text,
                            "marks": question_mark // 2
                        },

                        {
                            "label": "B",
                            "question": generate_b_question(
                                text.replace("?", "").strip(),
                                sec.get("difficulty")
                            ),
                            "marks": question_mark // 2
                        }

                    ]
                })

            # ---------- Normal Question ----------
            else:

                processed_questions.append(q)

        # ====================================================
        # ADD SECTION
        # ====================================================

        final_questions.append({

            "title": title,
            "type": qtype,
            "marks": question_mark,
            "attempt": attempt,
            "questions": processed_questions

        })

    # ====================================================
    # SAVE PAPER
    # ====================================================

    cur.execute("""
        INSERT INTO papers(
            teacher_id,
            university,
            exam_title,
            qp_code,
            subject,
            marks,
            exam_date,
            start_time,
            end_time,
            instructions,
            questions,
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        session["teacher_id"],
        university,
        exam_title,
        qp_code,
        subject,
        calculated_total_marks,
        exam_date,
        start_time,
        end_time,
        instructions,
        json.dumps(final_questions),
        datetime.now().strftime("%d-%m-%Y %H:%M")
    ))

    paper_id = cur.lastrowid

    conn.commit()
    conn.close()

    return redirect(f"/view_paper/{paper_id}")


# ================= QUESTION BANK PAGE =================
@app.route('/question_bank')
def question_bank():

    if "teacher_id" not in session:
        return redirect("/login")

    subject = request.args.get("subject")
    qtype = request.args.get("type")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM question_bank
        WHERE teacher_id=?
          AND COALESCE(is_deleted, 0)=0
          AND TRIM(COALESCE(question, '')) <> ''
        ORDER BY id ASC
    """, (session["teacher_id"],))

    rows = cur.fetchall()

    # Group questions
    data = group_questions(rows)

    # Create subject cards
    subjects = []

    total_questions = 0
    mcq_count = 0
    short_count = 0
    long_count = 0

    for subject_name, questions in data.items():

        total = (
            len(questions["MCQ"])
            + len(questions["Short Notes"])
            + len(questions["Long Answer"])
        )

        total_questions += total
        mcq_count += len(questions["MCQ"])
        short_count += len(questions["Short Notes"])
        long_count += len(questions["Long Answer"])

        subjects.append({
            "name": subject_name,
            "total": total
        })

    # Last 5 added questions
    recent_questions = list(reversed(rows[-5:]))

    conn.close()

    return render_template(
        "question_bank.html",
        data=data,
        subjects=subjects,
        total_questions=total_questions,
        mcq_count=mcq_count,
        short_count=short_count,
        long_count=long_count,
        recent_questions=recent_questions,
        selected_subject=subject,
        selected_type=qtype
    )


@app.route("/question_bank/view")
def question_bank_view():

    if "teacher_id" not in session:
        return redirect("/login")

    subject = request.args.get("subject", "").strip()
    raw_qtype = request.args.get("type", "ALL").strip().upper()
    type_aliases = {
        "ALL": None,
        "MCQ": ["MCQ"],
        "SHORT": ["SHORT", "SHORT NOTES", "SHORT ANSWER", "SHORTNOTE"],
        "LONG": ["LONG", "LONG ANSWER", "THEORY"]
    }
    qtype = raw_qtype if raw_qtype in type_aliases else "ALL"

    conn = get_db()
    cur = conn.cursor()

    if qtype == "ALL":
        cur.execute("""
            SELECT * FROM question_bank
            WHERE teacher_id=? AND subject=?
              AND COALESCE(is_deleted, 0)=0
              AND TRIM(COALESCE(question, '')) <> ''
            ORDER BY id ASC
        """, (session["teacher_id"], subject))
    else:
        placeholders = ",".join(["?"] * len(type_aliases[qtype]))
        cur.execute(f"""
            SELECT * FROM question_bank
            WHERE teacher_id=? AND subject=?
              AND UPPER(TRIM(COALESCE(type, ''))) IN ({placeholders})
              AND COALESCE(is_deleted, 0)=0
              AND TRIM(COALESCE(question, '')) <> ''
            ORDER BY id ASC
        """, (session["teacher_id"], subject, *type_aliases[qtype]))

    questions = cur.fetchall()

    conn.close()

    return render_template(
        "question_bank_view.html",
        subject=subject,
        qtype=qtype,
        questions=questions
    )
# ================= ADD BULK QUESTION =================
@app.route("/bulk_add_manual", methods=["POST"])
def bulk_add_manual():

    if "teacher_id" not in session:
        return redirect("/login")

    subject = request.form["subject"].strip()
    raw_type = request.form["type"].strip().upper()

    # Normalize type
    if raw_type == "MCQ":
        qtype = "MCQ"

    elif raw_type in ["SHORT", "SHORT NOTES", "SHORTNOTE"]:
        qtype = "SHORT"

    elif raw_type in ["LONG", "LONG ANSWER", "THEORY"]:
        qtype = "LONG"

    else:
        qtype = "LONG"

    count = int(request.form["count"])

    conn = get_db()
    cur = conn.cursor()

    for i in range(count):

        question = request.form.get(f"question_{i}")

        if not question:
            continue

        option_a = request.form.get(f"a_{i}")
        option_b = request.form.get(f"b_{i}")
        option_c = request.form.get(f"c_{i}")
        option_d = request.form.get(f"d_{i}")

        answer = request.form.get(f"answer_{i}")
        difficulty = request.form.get(f"difficulty_{i}", "").strip()

        cur.execute("""
            INSERT INTO question_bank(
                teacher_id,
                subject,
                type,
                question,
                option_a,
                option_b,
                option_c,
                option_d,
                answer,
                marks,
                difficulty
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (
            session["teacher_id"],
            subject,
            qtype,
            question,
            option_a,
            option_b,
            option_c,
            option_d,
            answer,
            1,
            difficulty
        ))

    conn.commit()
    conn.close()

    return redirect("/question_bank")

#===Add bulk
@app.route('/add_bulk_question', methods=['POST'])
def add_bulk_question():

    if "teacher_id" not in session:
        return redirect("/login")

    subject = request.form["subject"].strip()

    # Get type from form
    raw_type = request.form["type"].strip().upper()
    print("TYPE FROM FORM =", raw_type)

    # Normalize type
    if raw_type == "MCQ":
        qtype = "MCQ"

    elif raw_type == "SHORT":
        qtype = "SHORT"

    elif raw_type == "LONG":
        qtype = "LONG"

    else:
        qtype = "LONG"

    # Set marks
    if qtype == "MCQ":
        marks = 1

    elif qtype == "SHORT":
        marks = 4

    else:
        marks = 12

    conn = get_db()
    cur = conn.cursor()

    i = 0

    while True:

        if f"question_{i}" not in request.form:
            break

        question = request.form.get(f"question_{i}", "").strip()
        a = request.form.get(f"a_{i}", "").strip()
        b = request.form.get(f"b_{i}", "").strip()
        c = request.form.get(f"c_{i}", "").strip()
        d = request.form.get(f"d_{i}", "").strip()
        ans = request.form.get(f"ans_{i}", "").strip()
        difficulty = request.form.get(f"difficulty_{i}", "").strip()

        if not question:
            i += 1
            continue

        if qtype == "MCQ":
            if not all([a, b, c, d, ans]):
                i += 1
                continue

        uploaded = request.files.get(f"question_image_{i}")
        try:
            chart_image = save_question_upload(uploaded) if uploaded and uploaded.filename else ""
        except ValueError as exc:
            conn.close()
            return str(exc), 400
        chart_type = request.form.get(f"chart_type_{i}", "").strip().lower()
        if chart_type not in {"bar", "pie", "histogram", "line", "frequency polygon", "ogive", "diagram"}:
            chart_type = ""
        dataset = request.form.get(f"chart_data_{i}", "").strip()
        chart_data = None
        if dataset:
            try:
                chart_data = json.dumps(json.loads(dataset))
            except json.JSONDecodeError:
                chart_data = json.dumps({"description": dataset})
        # If structured data and a chart type are supplied, render a chart image automatically.
        if chart_type and chart_type != "diagram" and dataset:
            try:
                parsed = json.loads(dataset)
                generated = create_chart_image(chart_type, parsed, f"{subject} - Question Figure")
                if generated:
                    chart_image = generated
            except Exception:
                pass
        cur.execute("""
            INSERT INTO question_bank(
                teacher_id, subject, type, question, option_a, option_b, option_c, option_d,
                answer, marks, difficulty, chart_type, chart_data, chart_image
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (session["teacher_id"], subject, qtype, question, a, b, c, d, ans, marks,
              difficulty, chart_type, chart_data, chart_image))

        i += 1

    conn.commit()
    conn.close()

    return redirect("/question_bank")



@app.route("/question_bank/upload_image/<int:question_id>", methods=["POST"])
def upload_question_bank_image(question_id):
    if "teacher_id" not in session:
        return redirect("/login")
    uploaded = request.files.get("question_image")
    chart_type = request.form.get("chart_type", "").strip().lower()
    if chart_type and chart_type not in {"bar", "pie", "histogram", "line", "frequency polygon", "ogive", "diagram"}:
        chart_type = "diagram"
    if not uploaded or not uploaded.filename:
        return redirect(request.referrer or url_for("question_bank"))
    try:
        image_path = save_question_upload(uploaded)
    except ValueError as exc:
        return str(exc), 400
    conn = get_db()
    conn.execute("UPDATE question_bank SET chart_image=?, chart_type=CASE WHEN ?='' THEN chart_type ELSE ? END WHERE id=? AND teacher_id=?",
                 (image_path, chart_type, chart_type, question_id, session["teacher_id"]))
    conn.commit(); conn.close()
    return redirect(request.referrer or url_for("question_bank"))

# ================= ADD QUESTION =================
@app.route("/add_question", methods=["POST"])
def add_question():
    if require_login():
        return redirect("/login")

    subject = request.form["subject"].strip()
    qtype = request.form["type"]
    question = request.form["question"].strip()
    difficulty = request.form.get("difficulty", "").strip()

    if not question:
        return "Question cannot be empty"

    if qtype == "MCQ":
        if not all([
            request.form.get("option_a", "").strip(),
            request.form.get("option_b", "").strip(),
            request.form.get("option_c", "").strip(),
            request.form.get("option_d", "").strip()
        ]):
            return "All MCQ options are required"

    conn = get_db()
    cur = conn.cursor()

    uploaded = request.files.get("question_image")
    try:
        chart_image = save_question_upload(uploaded) if uploaded and uploaded.filename else ""
    except ValueError as exc:
        conn.close()
        return str(exc), 400
    chart_type = request.form.get("chart_type", "").strip().lower()
    chart_data_text = request.form.get("chart_data", "").strip()
    chart_data = None
    if chart_data_text:
        try: chart_data = json.dumps(json.loads(chart_data_text))
        except json.JSONDecodeError: chart_data = json.dumps({"description": chart_data_text})
    if chart_type in {"bar", "pie", "histogram", "line", "frequency polygon", "ogive"} and chart_data_text:
        try:
            generated = create_chart_image(chart_type, json.loads(chart_data_text), f"{subject} - Question Figure")
            if generated: chart_image = generated
        except Exception: pass
    cur.execute("""
    INSERT INTO question_bank(teacher_id, subject, type, question, option_a, option_b, option_c, option_d,
        answer, marks, difficulty, chart_type, chart_data, chart_image)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (session["teacher_id"], subject, qtype, question, request.form.get("option_a"),
          request.form.get("option_b"), request.form.get("option_c"), request.form.get("option_d"),
          request.form.get("answer"), request.form["marks"], difficulty, chart_type,
          chart_data, chart_image))

    conn.commit()
    conn.close()

    return redirect("/question_bank")


# ================= BANK UPDATE QUESTION =================
@app.route("/update_question/<int:id>", methods=["POST"])
def update_question(id):

    if "teacher_id" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    # Get the current subject and type before updating
    cur.execute("""
        SELECT subject, type
        FROM question_bank
        WHERE id=? AND teacher_id=?
    """, (
        id,
        session["teacher_id"]
    ))

    old = cur.fetchone()

    if not old:
        conn.close()
        return redirect("/question_bank")

    subject = old["subject"]
    qtype = old["type"]

    cur.execute("""
        UPDATE question_bank
        SET
            question=?,
            option_a=?,
            option_b=?,
            option_c=?,
            option_d=?,
            answer=?,
            marks=?,
            difficulty=?
        WHERE id=? AND teacher_id=?
    """, (
        request.form.get("question"),
        request.form.get("option_a"),
        request.form.get("option_b"),
        request.form.get("option_c"),
        request.form.get("option_d"),
        request.form.get("answer"),
        request.form.get("marks"),
        request.form.get("difficulty", "").strip(),
        id,
        session["teacher_id"]
    ))

    conn.commit()
    conn.close()

    return redirect(
    f"/question_bank/view?subject={subject}&type={qtype}"
)


# ================= DELETE QUESTION =================
@app.route("/delete_question/<int:id>")
def delete_question(id):

    if "teacher_id" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    # Get subject and type before deleting
    cur.execute("""
        SELECT subject, type
        FROM question_bank
        WHERE id=? AND teacher_id=?
    """, (
        id,
        session["teacher_id"]
    ))

    row = cur.fetchone()

    if not row:
        conn.close()
        return redirect("/question_bank")

    subject = row["subject"]
    qtype = row["type"]

    # Delete question
    cur.execute("""
        DELETE FROM question_bank
        WHERE id=? AND teacher_id=?
    """, (
        id,
        session["teacher_id"]
    ))

    conn.commit()
    conn.close()

    return redirect(
        f"/question_bank/view?subject={subject}&type={qtype}"
    )


# ================= QUESTION BANK BULK CHANGES =================
@app.route("/question_bank/save_changes", methods=["POST"])
def question_bank_save_changes():
    if "teacher_id" not in session:
        return redirect("/login")

    teacher_id = session["teacher_id"]
    conn = get_db()
    cur = conn.cursor()

    # Delete selected subjects from the subject-list page.
    subjects_to_delete = request.form.getlist("deleted_subjects")
    for subject in subjects_to_delete:
        cur.execute("DELETE FROM question_bank WHERE teacher_id=? AND subject=?", (teacher_id, subject))

    # Delete selected questions from a subject/type page.
    delete_ids = []
    for raw_id in request.form.getlist("delete_ids"):
        try:
            delete_ids.append(int(raw_id))
        except ValueError:
            pass
    for qid in delete_ids:
        cur.execute("DELETE FROM question_bank WHERE id=? AND teacher_id=?", (qid, teacher_id))

    # Save edits for visible questions. The question id is the suffix in the form field.
    ids = set()
    for key in request.form.keys():
        if key.startswith("question_"):
            try:
                ids.add(int(key.split("_", 1)[1]))
            except ValueError:
                pass

    for qid in ids:
        if qid in delete_ids:
            continue
        row = cur.execute("SELECT id FROM question_bank WHERE id=? AND teacher_id=?", (qid, teacher_id)).fetchone()
        if not row:
            continue
        question = request.form.get(f"question_{qid}", "").strip()
        marks = request.form.get(f"marks_{qid}", "1").strip()
        try:
            marks_value = max(1, int(marks))
        except ValueError:
            marks_value = 1
        uploaded = request.files.get(f"question_image_{qid}")
        image_path = None
        if uploaded and uploaded.filename:
            try:
                image_path = save_question_upload(uploaded)
            except ValueError as exc:
                conn.close()
                return str(exc), 400
        chart_type = request.form.get(f"chart_type_{qid}", "").strip().lower()
        if chart_type not in {"bar", "pie", "histogram", "line", "frequency polygon", "ogive", "diagram"}:
            chart_type = "diagram"
        if image_path:
            cur.execute("""
                UPDATE question_bank SET question=?, option_a=?, option_b=?, option_c=?, option_d=?, answer=?, marks=?, difficulty=?, chart_image=?, chart_type=?
                WHERE id=? AND teacher_id=?
            """, (question, request.form.get(f"option_a_{qid}", "").strip(),
                  request.form.get(f"option_b_{qid}", "").strip(), request.form.get(f"option_c_{qid}", "").strip(),
                  request.form.get(f"option_d_{qid}", "").strip(), request.form.get(f"answer_{qid}", "").strip(),
                  marks_value, request.form.get(f"difficulty_{qid}", "").strip(), image_path, chart_type, qid, teacher_id))
        else:
            cur.execute("""
                UPDATE question_bank SET question=?, option_a=?, option_b=?, option_c=?, option_d=?, answer=?, marks=?, difficulty=?
                WHERE id=? AND teacher_id=?
            """, (question, request.form.get(f"option_a_{qid}", "").strip(),
                  request.form.get(f"option_b_{qid}", "").strip(), request.form.get(f"option_c_{qid}", "").strip(),
                  request.form.get(f"option_d_{qid}", "").strip(), request.form.get(f"answer_{qid}", "").strip(),
                  marks_value, request.form.get(f"difficulty_{qid}", "").strip(), qid, teacher_id))

    conn.commit()
    conn.close()

    subject = request.form.get("subject", "").strip()
    qtype = request.form.get("qtype", "ALL").strip().upper()
    if subject:
        return redirect(url_for("question_bank_view", subject=subject, type=qtype))
    return redirect(url_for("question_bank"))


@app.route("/question_bank/delete_subject", methods=["POST"])
def question_bank_delete_subject():
    if "teacher_id" not in session:
        return redirect("/login")
    subject = request.form.get("subject", "").strip()
    if subject:
        conn = get_db()
        conn.execute("DELETE FROM question_bank WHERE teacher_id=? AND subject=?", (session["teacher_id"], subject))
        conn.commit()
        conn.close()
    return redirect("/question_bank")


# ================= SAVE AND EXIT =================
@app.route("/save_exit")
def save_exit():
    if require_login():
        return redirect("/login")

    return redirect("/dashboard")


# ================= EDIT QUESTION =================
@app.route("/edit_question/<int:id>")
def edit_question(id):
    if require_login():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    SELECT *
    FROM question_bank
    WHERE id=? AND teacher_id=?
    """, (
        id,
        session["teacher_id"]
    ))

    question = cur.fetchone()
    conn.close()

    return render_template(
        "edit_question.html",
        question=question
    )


# ================= PAPERS LIST =================
@app.route("/papers")
def papers():
    if require_login():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM papers
        WHERE teacher_id=?
    """, (session["teacher_id"],))

    papers = cur.fetchall()
    conn.close()

    return render_template("papers.html", papers=papers)


# ================= GENERATE PAPER =================
@app.route("/generate", methods=["POST"])
def generate():
    if require_login():
        return redirect("/login")

    university = request.form["university"]
    exam_title = request.form["exam_title"]
    qp_code = request.form["qp_code"]

    subject = request.form["subject"]
    marks = int(request.form["marks"])
    exam_date = request.form["exam_date"]

    start_time = request.form["start_time"]
    end_time = request.form["end_time"]

    question_marks = request.form["question_marks"]
    difficulty = normalize_difficulty(request.form["difficulty"])
    syllabus = request.form.get("syllabus", "")

    qtype = request.form["type"]
    count = int(request.form["count"])

    err = validate_form(marks, exam_date)
    if err:
        return err

    # Generate questions using AI
    questions = generate_ai(subject, qtype, count, difficulty=difficulty, syllabus=syllabus)

    # Fallback to question bank if AI fails
    if not questions:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT question,
                   option_a,
                   option_b,
                   option_c,
                   option_d,
                   type,
                   difficulty
            FROM question_bank
            WHERE teacher_id=?
            AND subject=?
            AND type=?
            AND LOWER(TRIM(COALESCE(difficulty, ''))) = LOWER(TRIM(?))
            LIMIT ?
        """, (
            session["teacher_id"],
            subject,
            qtype,
            difficulty,
            count
        ))

        rows = cur.fetchall()
        conn.close()

        questions = []
        for r in rows:
            if r["type"] == "MCQ":
                questions.append({
                    "question": r["question"],
                    "options": [
                        r["option_a"],
                        r["option_b"],
                        r["option_c"],
                        r["option_d"]
                    ],
                    "type": "MCQ",
                    "difficulty": r["difficulty"] or "Unclassified"
                })
            else:
                questions.append({
                    "question": r["question"],
                    "type": r["type"],
                    "difficulty": r["difficulty"] or "Unclassified"
                })

    if not questions:
        return "No questions available. Add questions in Question Bank or fix API key."

    session["last_questions"] = json.dumps(questions)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO papers (
        teacher_id, university, exam_title, qp_code, subject,
        marks, exam_date, start_time, end_time, question_marks,
        difficulty, syllabus, qtype, questions, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session["teacher_id"], university, exam_title, qp_code, subject,
        marks, exam_date, start_time, end_time, question_marks,
        difficulty, syllabus, qtype, json.dumps(questions),
        datetime.now().strftime("%d-%m-%Y %H:%M")
    ))

    paper_id = cur.lastrowid
    conn.commit()
    conn.close()

    return redirect(f"/view_paper/{paper_id}")


# ================= VIEW PAPER =================
@app.route("/view_paper/<int:paper_id>")
def view_paper(paper_id):

    if require_login():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM papers
        WHERE id=?
        AND teacher_id=?
    """, (
        paper_id,
        session["teacher_id"]
    ))

    paper = cur.fetchone()
    conn.close()

    if not paper:
        return "Paper not found"

    paper = dict(paper)

    # ================= FORMAT DATE =================
    try:
        paper["exam_date"] = datetime.strptime(
            paper["exam_date"],
            "%Y-%m-%d"
        ).strftime("%d %B %Y")
    except:
        pass

    # ================= LOAD QUESTIONS =================
    questions = json.loads(paper["questions"])

    # ================= BACKWARD COMPATIBILITY FIX =================
    # old key: section → new key: title
    for sec in questions:
        if isinstance(sec, dict):
            if "section" in sec and "title" not in sec:
                sec["title"] = sec["section"]

    # ================= DETECT SECTION STRUCTURE =================
    sections = None

    if (
        isinstance(questions, list)
        and len(questions) > 0
        and isinstance(questions[0], dict)
        and "title" in questions[0]
    ):
        sections = questions

    show_section = False

    if sections and len(sections) > 1:
        show_section = True

    return render_template(
        "view_paper.html",
        paper=paper,
        questions=questions,
        sections=sections,
        show_section=show_section
    )


# ================= MODIFY =================
@app.route("/modify/<int:id>")
def modify(id):

    if require_login():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM papers
        WHERE id=? AND teacher_id=?
    """, (id, session["teacher_id"]))

    row = cur.fetchone()

    conn.close()

    if not row:
        return "Paper not found"

    paper = dict(row)

    # Load complete paper JSON
    try:
        sections = json.loads(paper["questions"])
    except:
        sections = []

    return render_template(
        "modify.html",
        paper=paper,
        sections=sections
    )


# ================= SAVE MODIFY =================

@app.route("/save_modify", methods=["POST"])
def save_modify():
    if require_login():
        return redirect("/login")

    paper_id = request.form["paper_id"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT questions
        FROM papers
        WHERE id=? AND teacher_id=?
    """,(paper_id,session["teacher_id"]))

    row = cur.fetchone()
    if not row:
        conn.close()
        return "Paper not found"

    sections = json.loads(row["questions"])

    # Header fields
    university = request.form["university"]
    exam_title = request.form["exam_title"]
    qp_code = request.form["qp_code"]
    subject = request.form["subject"]
    marks = request.form["marks"]
    exam_date = request.form.get("exam_date","")
    start_time = request.form.get("start_time","")
    end_time = request.form.get("end_time","")
    instructions = request.form.get("instructions","")

    # Lists from form
    section_titles = request.form.getlist("section_title")
    attempts = request.form.getlist("attempt")
    section_marks = request.form.getlist("section_marks")

    questions = request.form.getlist("question")
    qmarks = request.form.getlist("question_marks")

    sub_questions = request.form.getlist("sub_question")
    sub_marks = request.form.getlist("sub_marks")

    qi = 0
    si = 0

    for i, sec in enumerate(sections):
        if i < len(section_titles):
            sec["title"] = section_titles[i]
        if i < len(attempts):
            sec["attempt"] = int(attempts[i] or 0)
        if i < len(section_marks):
            sec["marks"] = int(section_marks[i] or 1)

        for q in sec.get("questions",[]):
            if q.get("subparts"):
                for sp in q["subparts"]:
                    if si < len(sub_questions):
                        sp["question"] = sub_questions[si]
                    if si < len(sub_marks):
                        sp["marks"] = int(sub_marks[si] or 1)
                    si += 1
            else:
                if qi < len(questions):
                    q["question"] = questions[qi]
                if qi < len(qmarks):
                    q["marks"] = int(qmarks[qi] or sec["marks"])
                qi += 1

    calculated_total_marks = sum(
        int(sec.get("attempt", 0) or 0) * int(sec.get("marks", 0) or 0)
        for sec in sections
        if isinstance(sec, dict)
    )

    cur.execute("""
        UPDATE papers
        SET university=?,
            exam_title=?,
            qp_code=?,
            subject=?,
            marks=?,
            exam_date=?,
            start_time=?,
            end_time=?,
            instructions=?,
            questions=?
        WHERE id=? AND teacher_id=?
    """,(
        university,
        exam_title,
        qp_code,
        subject,
        calculated_total_marks,
        exam_date,
        start_time,
        end_time,
        instructions,
        json.dumps(sections),
        paper_id,
        session["teacher_id"]
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("view_paper", paper_id=paper_id))



# ================= DELETE PAPER =================
@app.route("/delete_paper/<int:id>")
def delete_paper(id):
    if require_login():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM papers
        WHERE id=? AND teacher_id=?
    """, (
        id,
        session["teacher_id"]
    ))

    conn.commit()
    conn.close()

    return redirect("/papers")


# ================= DOWNLOAD PDF (LAST QUESTIONS) =================
@app.route("/download_pdf")
def download_pdf():
    if require_login():
        return redirect("/login")

    pdf = canvas.Canvas("paper.pdf")
    y = 800

    questions = json.loads(session.get("last_questions", "[]"))

    for i, q in enumerate(questions):
        pdf.drawString(50, y, f"Q{i+1}: {q['question']}")
        y -= 20

    pdf.save()
    return send_file("paper.pdf", as_attachment=True)


#Previous Paper====#
@app.route("/previous_paper")
def previous_paper():

    # Check Login
    if require_login():
        return redirect("/login")

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    teacher_id = session["teacher_id"]

    # Fetch latest paper of logged-in teacher
    cur.execute("""
        SELECT *
        FROM papers
        WHERE teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (teacher_id,))

    paper = cur.fetchone()

    # Debugging
    if paper:
        print("========== PREVIOUS PAPER ==========")
        print("Teacher ID :", teacher_id)
        print("Paper ID   :", paper["id"])
        print("Subject    :", paper["subject"])
        print("Exam       :", paper["exam"])
        print("Marks      :", paper["marks"])
        print("===================================")
    else:
        print("========== PREVIOUS PAPER ==========")
        print("Teacher ID :", teacher_id)
        print("No previous paper found.")
        print("===================================")

    conn.close()

    return render_template(
        "previous.html",
        paper=paper
    )
#===================PROFILE===============
@app.route("/profile")
def profile():

    if "teacher_id" not in session:
        return redirect("/login")

    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row

    teacher_id = session["teacher_id"]

    # Teacher Details
    teacher = conn.execute(
        "SELECT * FROM teachers WHERE id=?",
        (teacher_id,)
    ).fetchone()

    # Papers Generated
    total = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE teacher_id=?",
        (teacher_id,)
    ).fetchone()[0]

    # Questions Added
    total_questions = conn.execute(
        "SELECT COUNT(*) FROM question_bank WHERE teacher_id=?",
        (teacher_id,)
    ).fetchone()[0]

    # AI Generated Papers
    ai_generated = 0

    conn.close()

    paper_percent = min(total * 10, 100)
    question_percent = min(total_questions / 5, 100)
    ai_percent = min(ai_generated * 10, 100)

    return render_template(
        "profile.html",
        teacher=teacher,
        total=total,
        total_questions=total_questions,
        ai_generated=ai_generated,
        paper_percent=paper_percent,
        question_percent=question_percent,
        ai_percent=ai_percent
    )


#==========UPDATE
@app.route("/update_profile", methods=["POST"])
def update_profile():

    if "teacher_id" not in session:
        return redirect("/login")

    teacher_id = session["teacher_id"]

    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    college = request.form.get("college", "").strip()
    department = request.form.get("department", "").strip()

    if not valid_letters(name):
        return redirect("/profile?error=name")
    if not re.fullmatch(r"[6-9]\d{9}", phone):
        return redirect("/profile?error=phone")
    if college and not valid_profile_text(college):
        return redirect("/profile?error=college")
    if department and not valid_profile_text(department):
        return redirect("/profile?error=department")

    conn = sqlite3.connect("database.db")

    conn.execute("""
        UPDATE teachers
        SET
            name=?,
            phone=?,
            college=?,
            department=?
        WHERE id=?
    """, (
        name,
        phone,
        college,
        department,
        teacher_id
    ))

    conn.commit()
    conn.close()

    return redirect("/profile?updated=profile")
#==========update profilephoto
@app.route("/upload_profile", methods=["POST"])
def upload_profile():
    if "teacher_id" not in session:
        return redirect("/login")

    file = request.files.get("profile_photo")
    if not file or not file.filename:
        return redirect("/profile?error=photo")

    allowed = {"png", "jpg", "jpeg", "webp"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        return redirect("/profile?error=photo")

    folder = os.path.join("static", "uploads", "profile")
    os.makedirs(folder, exist_ok=True)
    filename = f"teacher_{session['teacher_id']}_{uuid.uuid4().hex[:10]}.{ext}"
    file.save(os.path.join(folder, filename))

    conn = get_db()
    old = conn.execute("SELECT profile_image FROM teachers WHERE id=?", (session["teacher_id"],)).fetchone()
    conn.execute("UPDATE teachers SET profile_image=? WHERE id=?", (filename, session["teacher_id"]))
    conn.commit()
    conn.close()

    # Remove the previous custom image after the DB update. Keep default.png.
    if old and old["profile_image"] and old["profile_image"] != "default.png":
        old_path = os.path.join(folder, old["profile_image"])
        if os.path.isfile(old_path) and old["profile_image"] != filename:
            try:
                os.remove(old_path)
            except OSError:
                pass

    return redirect("/profile?updated=photo")
#==================Logout==============
@app.route("/logout")
def logout():

    print("========== LOGOUT CLICKED ==========")

    session.clear()

    return redirect("/")
# ================= SPECIFIC PAPER DOWNLOAD =================
@app.route("/download_paper_pdf/<int:id>")
def download_paper_pdf(id):

    if require_login():
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM papers
        WHERE id=? AND teacher_id=?
    """, (
        id,
        session["teacher_id"]
    ))

    paper = cur.fetchone()
    conn.close()

    if not paper:
        return "Paper not found"

    questions = json.loads(paper["questions"])

    filename = f"paper_{id}.pdf"
    pdf = canvas.Canvas(filename)

    pdf.setTitle("Question Paper")

    PAGE_WIDTH = 595
    LEFT = 50
    RIGHT = 545

    y = 800

    # ================= HEADER =================

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(PAGE_WIDTH/2, y, paper["university"])
    y -= 20

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(PAGE_WIDTH/2, y, paper["exam_title"])
    y -= 22

    pdf.setFont("Helvetica", 11)

    pdf.drawString(LEFT, y, f"QP Code : {paper['qp_code']}")
    y -= 18

    pdf.drawString(LEFT, y, f"Date : {paper['exam_date']}")
    pdf.drawRightString(
        RIGHT,
        y,
        f"Time : {paper['start_time']} - {paper['end_time']}"
    )
    y -= 18

    pdf.drawString(LEFT, y, f"Subject : {paper['subject']}")
    pdf.drawRightString(
        RIGHT,
        y,
        f"Total Marks : {paper['marks']}"
    )
    y -= 28

    # ================= INSTRUCTIONS =================

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(LEFT, y, "Instructions")
    y -= 18

    pdf.setFont("Helvetica", 10)

    import re

    instruction_no = 1

    for line in paper["instructions"].split("\n"):

        line = line.strip()

        if not line:
            continue

        line = re.sub(r'^\d+[\.\)]?\s*', '', line)

        pdf.drawString(
            LEFT + 10,
            y,
            f"{instruction_no}) {line}"
        )

        instruction_no += 1
        y -= 15

    y -= 12
    q_no = 1

    # ================= SECTION LOOP =================

    from reportlab.lib.utils import simpleSplit

    for sec in questions:

        pdf.setFont("Helvetica-Bold", 12)

        pdf.drawString(LEFT, y, sec.get("title", ""))
        y -= 18

        pdf.setFont("Helvetica", 11)

        pdf.drawString(
            LEFT,
            y,
            f"Attempt any {sec.get('attempt',0)} question(s)"
        )

        y -= 24

        # ================= SHORT NOTES =================
        if sec.get("type") == "Short Notes":

            pdf.setFont("Helvetica-Bold", 11)

            pdf.drawString(LEFT, y, f"Q{q_no}.")

            total_marks = int(sec.get("marks", 1)) * int(sec.get("attempt", 0))

            pdf.drawRightString(
                RIGHT,
                y,
                f"[{total_marks}]"
            )

            y -= 18

            pdf.setFont("Helvetica", 11)

            for i, q in enumerate(sec.get("questions", [])):

                letter = chr(65 + i)

                pdf.drawString(
                    LEFT + 30,
                    y,
                    f"{letter}) {q['question']}"
                )
                y -= 18

                if q.get("chart_image"):
                    try:
                        from reportlab.lib.utils import ImageReader
                        chart_file = chart_file_path(q.get("chart_image", ""))
                        if os.path.exists(chart_file):
                            img = ImageReader(chart_file)
                            iw, ih = img.getSize()
                            scale = min(390/iw, 170/ih)
                            dw, dh = iw*scale, ih*scale
                            if y < dh + 70:
                                pdf.showPage(); y = 800
                            pdf.drawImage(chart_file, LEFT + 45, y-dh, width=dw, height=dh, preserveAspectRatio=True, mask='auto')
                            y -= dh + 10
                    except Exception as e:
                        print("PDF SHORT GRAPH ERROR:", e)

            y -= 10
            q_no += 1
            continue

        # ================= OTHER TYPES =================

        for q in sec.get("questions", []):

            # ---------- Split A/B ----------
            if q.get("subparts"):

                pdf.setFont("Helvetica-Bold", 11)
                pdf.drawString(LEFT, y, f"Q{q_no}.")
                y -= 18

                pdf.setFont("Helvetica", 11)

                for sub in q["subparts"]:

                    text = f"{sub['label']}) {sub['question']}"

                    lines = simpleSplit(text, "Helvetica", 11, 430)
                    mark_y = y
                    for line in lines:

                        pdf.drawString(LEFT + 30, y, line)
                        y -= 15

                    pdf.drawRightString(RIGHT, mark_y, f"[{sub['marks']}]")
                    y -= 8

                q_no += 1

            else:

                pdf.setFont("Helvetica-Bold", 11)

                text = f"Q{q_no}. {q['question']}"

                lines = simpleSplit(text, "Helvetica-Bold", 11, 440)

                for i, line in enumerate(lines):

                    pdf.drawString(LEFT, y, line)

                    if i == 0:
                        pdf.drawRightString(
                            RIGHT,
                            y,
                            f"[{q.get('marks', sec.get('marks',1))}]"
                        )

                    y -= 15

                if q.get("chart_image"):
                    try:
                        from reportlab.lib.utils import ImageReader
                        chart_file = chart_file_path(q.get("chart_image", ""))
                        if os.path.exists(chart_file):
                            if y < 260:
                                pdf.showPage(); y = 800
                            img = ImageReader(chart_file)
                            iw, ih = img.getSize()
                            max_w, max_h = 430, 220
                            scale = min(max_w/iw, max_h/ih)
                            dw, dh = iw*scale, ih*scale
                            pdf.drawImage(chart_file, LEFT+20, y-dh, width=dw, height=dh, preserveAspectRatio=True, mask='auto')
                            y -= dh + 12
                    except Exception as e:
                        print("PDF CHART ERROR:", e)

                if q.get("options"):

                    pdf.setFont("Helvetica", 11)

                    for j, op in enumerate(q["options"]):

                        pdf.drawString(
                            LEFT + 30,
                            y,
                            f"{chr(65+j)}) {op}"
                        )

                        y -= 15

                y -= 10
                q_no += 1

            if y < 90:
                pdf.showPage()
                y = 800
                pdf.setFont("Helvetica", 11)

    pdf.save()

    return send_file(
        filename,
        as_attachment=True,
        download_name=f"{paper['subject']}_Question_Paper.pdf"
    )
    
#====BOT
# ================= AI AGENT : GENERATE QUESTIONS FROM SYLLABUS =================

@app.route("/api/generate_mcqs_from_syllabus", methods=["POST"])
def generate_mcqs_from_syllabus_api():

    try:

        # ================= GET DATA =================

        pdf = request.files.get("syllabus")

        count = int(request.form.get("count", 10))

        question_type = request.form.get(
            "question_type",
            "MCQ"
        )

        subject = request.form.get(
            "subject",
            "Unknown"
        )

        teacher_id = session.get("teacher_id")
        if not teacher_id:
            return jsonify({"success": False, "message": "Please login again."}), 401


        # ================= CHECK PDF =================

        if not pdf:

            return jsonify({

                "success": False,

                "message": "No PDF uploaded"

            })


        # ================= SAVE PDF =================

        filename = secure_filename(
            pdf.filename
        )

        upload_folder = "uploads"

        os.makedirs(
            upload_folder,
            exist_ok=True
        )


        pdf_path = os.path.join(
            upload_folder,
            filename
        )


        pdf.save(pdf_path)


        # ================= EXTRACT SYLLABUS =================

        syllabus_text = extract_pdf_text(
            pdf_path
        )


        if not syllabus_text.strip():

            return jsonify({

                "success": False,

                "message": "Could not extract text from PDF"

            })


        # ================= GENERATE USING GEMINI =================

        if question_type == "MCQ":

            ai_text = generate_mcqs_from_syllabus(
                syllabus_text,
                count
            )


        else:

            return jsonify({

                "success": False,

                "message": "Currently only MCQ supported"

            })


        # ================= AI FAILED =================

        if not ai_text:

            return jsonify({

                "success": False,

                "message": "AI generation failed"

            })


        # ================= PARSE QUESTIONS =================

        questions = parse_mcqs(
            ai_text
        )


        if not questions:

            return jsonify({

                "success": False,

                "message": "No questions parsed"

            })


        # ================= SAVE TO QUESTION BANK =================

        conn = get_db()

        cur = conn.cursor()


        for q in questions:

            cur.execute("""

                INSERT INTO question_bank(

                    teacher_id,
                    subject,
                    type,
                    question,
                    option_a,
                    option_b,
                    option_c,
                    option_d,
                    answer,
                    marks

                )

                VALUES(?,?,?,?,?,?,?,?,?,?)

            """,

            (

                teacher_id,

                subject,

                "MCQ",

                q.get("question"),

                q.get("a"),

                q.get("b"),

                q.get("c"),

                q.get("d"),

                q.get("answer"),

                1

            ))


        conn.commit()

        conn.close()



        # ================= RESPONSE =================

        return jsonify({

            "success": True,

            "generated": len(questions),

            "message":
            f"{len(questions)} MCQs added successfully."

        })


    except Exception as e:


        print(
            "AI AGENT ERROR:",
            e
        )


        return jsonify({

            "success": False,

            "message": str(e)

        })
def parse_mcqs(ai_text):

    questions = []

    blocks = ai_text.split("Question:")

    for block in blocks:

        block = block.strip()

        if not block:
            continue

        lines = [
            line.strip()
            for line in block.split("\n")
            if line.strip()
        ]

        if len(lines) < 6:
            continue

        question = lines[0]

        option_a = lines[1].replace("A)", "").strip()
        option_b = lines[2].replace("B)", "").strip()
        option_c = lines[3].replace("C)", "").strip()
        option_d = lines[4].replace("D)", "").strip()

        answer = lines[5].replace("Answer:", "").strip()

        questions.append({
            "question": question,
            "a": option_a,
            "b": option_b,
            "c": option_c,
            "d": option_d,
            "answer": answer
        })

    return questions

# ================= AI QUESTION BANK ASSISTANT =================


def init_ai_assistant_table():
    """Create one temporary draft row per teacher for the chatbot workflow."""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_question_drafts (
            teacher_id INTEGER PRIMARY KEY,
            subject TEXT,
            question_type TEXT,
            count INTEGER,
            difficulty TEXT,
            syllabus_text TEXT,
            questions_json TEXT,
            status TEXT DEFAULT 'collecting',
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()


init_ai_assistant_table()


def get_ai_draft(teacher_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM ai_question_drafts WHERE teacher_id=?",
        (teacher_id,)
    ).fetchone()
    conn.close()
    return row


def upsert_ai_draft(teacher_id, **fields):
    current = get_ai_draft(teacher_id)
    values = {
        "subject": current["subject"] if current else None,
        "question_type": current["question_type"] if current else None,
        "count": current["count"] if current else None,
        "difficulty": current["difficulty"] if current else None,
        "syllabus_text": current["syllabus_text"] if current else None,
        "questions_json": current["questions_json"] if current else None,
        "status": current["status"] if current else "collecting",
    }
    values.update(fields)

    conn = get_db()
    conn.execute("""
        INSERT INTO ai_question_drafts
        (teacher_id, subject, question_type, count, difficulty, syllabus_text,
         questions_json, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(teacher_id) DO UPDATE SET
            subject=excluded.subject,
            question_type=excluded.question_type,
            count=excluded.count,
            difficulty=excluded.difficulty,
            syllabus_text=excluded.syllabus_text,
            questions_json=excluded.questions_json,
            status=excluded.status,
            updated_at=excluded.updated_at
    """, (
        teacher_id,
        values["subject"],
        values["question_type"],
        values["count"],
        values["difficulty"],
        values["syllabus_text"],
        values["questions_json"],
        values["status"],
        datetime.now().isoformat(timespec="seconds")
    ))
    conn.commit()
    conn.close()


def reset_ai_draft(teacher_id):
    conn = get_db()
    conn.execute("DELETE FROM ai_question_drafts WHERE teacher_id=?", (teacher_id,))
    conn.commit()
    conn.close()


def clean_json_response(text):
    """Extract a JSON array from Gemini or local Ollama even if wrapped in markdown fences."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\[.*\]", text, flags=re.S)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except Exception:
        return None


def generate_question_bank_questions(syllabus_text, subject, question_type, count, difficulty):
    type_rules = {
        "MCQ": """Each item must contain: question, options (array of exactly 4 strings), answer, answer_key, marks, difficulty, graph.""",
        "Short Answer": """Each item must contain: question, options (empty array), answer, answer_key, marks, difficulty, graph.""",
        "Long Answer": """Each item must contain: question, options (empty array), answer, answer_key, marks, difficulty, graph."""
    }

    stats_graph_rules = ""
    if is_statistics_subject(subject, syllabus_text):
        stats_graph_rules = """
Statistics graph rules: when syllabus supports it, include data/chart questions. For chart-construction questions, put the complete dataset directly in the question as a readable two-column or multi-column table using newline-separated rows; never put the dataset only in hidden graph metadata or as a comma-separated sentence. Ask students to draw/construct the chart from the supplied data and DO NOT display a finished chart image for those questions. For pie-chart construction include category and frequency/value columns and ask students to calculate sector angles. For histograms include class intervals and frequencies. For ogive include cumulative frequency where relevant. Include graph:{type,title,xlabel,ylabel,data} with valid numeric data as metadata too. Only show a rendered chart for questions that explicitly ask the student to interpret/read an already drawn chart. Use formulas/symbols such as x̄, Σx, σ², μ, N, f and cf where relevant.
"""

    prompt = f"""
You are an expert university question paper setter.
Generate exactly {count} questions for the subject: {subject}.
Question type: {question_type}.
Difficulty: {difficulty}.
{question_style_rules(difficulty, question_type)}
If Difficulty is Mixed, distribute the questions as evenly as possible among Easy, Medium and Hard and set the difficulty field accordingly.

Generate questions ONLY from the syllabus supplied below. Do not invent topics outside it.
Syllabus:
----------------
{syllabus_text[:30000]}
----------------

Question quality requirements:
- Short-answer questions must be focused, specific, syllabus-aligned, and answerable in 2–5 sentences or a small calculation.
- Avoid vague, incomplete, duplicated, or factually incorrect questions. Numerical questions must include all needed data and units.
- Include a correct concise model answer. For MCQs, answer and answer_key must agree with exactly one of the four options.
- For statistics datasets, write an actual columnar table in the question text with headings and one data row per line.

{type_rules[question_type]}
{stats_graph_rules}
General rules:
1. Generate exactly {count} independent questions.
2. Use clear university examination language.
3. Do not include markdown, numbering, explanations, or extra text outside the JSON.
4. Return ONLY a valid JSON array.
5. For MCQ, all four options must be plausible and only one must be correct.

JSON format:
[
  {{
    "question": "...",
    "options": ["...", "...", "...", "..."],
    "answer": "...",
    "answer_key": "A",
    "marks": 1
  }}
]
"""

    try:
        ai_text = None
        if client:
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )
                ai_text = getattr(response, "text", None)
            except Exception as gemini_error:
                print("GEMINI QUESTION-BANK ERROR; trying local Ollama:", gemini_error)
        if not ai_text:
            ai_text = call_local_model(prompt)
        data = clean_json_response(ai_text)
        if not isinstance(data, list):
            return None

        cleaned = []
        level_pattern = mixed_difficulties(count) if normalize_difficulty(difficulty) == "Mixed" else [normalize_difficulty(difficulty) or "Medium"] * count
        for idx, item in enumerate(data[:count]):
            if not isinstance(item, dict) or not item.get("question"):
                continue
            options = item.get("options") or []
            if question_type == "MCQ" and len(options) != 4:
                continue
            item_diff = level_pattern[min(idx, len(level_pattern) - 1)]
            q = {
                "question": str(item.get("question", "")).strip(),
                "options": [str(x).strip() for x in options],
                "answer": str(item.get("answer", "")).strip(),
                "answer_key": str(item.get("answer_key", "")).strip(),
                "marks": int(item.get("marks", 1 if question_type == "MCQ" else 2)),
                "difficulty": item_diff
            }
            q = enrich_graph_question({**q, "graph": item.get("graph", {})}, subject, item_diff)
            cleaned.append(q)

        return cleaned if len(cleaned) == count else None
    except Exception as e:
        print("AI QUESTION BANK ERROR:", e)
        return None


def save_ai_questions(teacher_id, subject, question_type, questions):
    type_db = {
        "MCQ": "MCQ",
        "Short Answer": "Short Notes",
        "Long Answer": "Long Answer"
    }
    db_type = type_db.get(question_type, question_type)
    subject = (subject or "").strip()

    conn = get_db()
    cur = conn.cursor()
    saved = 0

    for q in questions:
        question = str(q.get("question", "")).strip()
        if not question:
            continue

        options = q.get("options", []) or []
        answer = str(q.get("answer", "")).strip()
        marks = int(q.get("marks", 1) or 1)
        difficulty = normalize_difficulty(q.get("difficulty"))
        chart_type = str(q.get("chart_type", "")).strip()
        chart_data = q.get("chart_data") or {}
        chart_image = str(q.get("chart_image", "")).strip()
        formula = str(q.get("formula", "")).strip()

        # Prevent duplicate insertion when the assistant is retried or refreshed.
        exists = cur.execute("""
            SELECT id FROM question_bank
            WHERE teacher_id=? AND subject=? AND type=? AND question=?
              AND COALESCE(is_deleted, 0)=0
            LIMIT 1
        """, (teacher_id, subject, db_type, question)).fetchone()

        if exists:
            continue

        cur.execute("""
            INSERT INTO question_bank(
                teacher_id, subject, type, question,
                option_a, option_b, option_c, option_d,
                answer, marks, difficulty, chart_type, chart_data, chart_image, formula, is_deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            teacher_id, subject, db_type, question,
            options[0] if len(options) > 0 else None,
            options[1] if len(options) > 1 else None,
            options[2] if len(options) > 2 else None,
            options[3] if len(options) > 3 else None,
            answer, marks, difficulty, chart_type, json.dumps(chart_data) if chart_data else None, chart_image, formula
        ))
        saved += 1

    conn.commit()
    conn.close()
    return saved


def assistant_reply_for_state(draft):
    if not draft or not draft["subject"]:
        return "What is the subject?"
    if not draft["syllabus_text"]:
        return "Great. Now upload the syllabus file using the 📎 Syllabus File button."
    if not draft["question_type"]:
        return "Which question type do you want? **MCQ**, **Short Answer**, or **Long Answer**?"
    if not draft["count"]:
        return "How many questions should I generate?"
    if not draft["difficulty"]:
        return "What difficulty level do you want: **Easy**, **Medium**, **Hard**, or **Mixed**?"
    return "Everything is ready. Type **generate** to create the questions."


@app.route("/api/ai-assistant/reset", methods=["POST"])
def ai_assistant_reset():
    if require_login():
        return jsonify({"success": False, "message": "Please login again."}), 401
    reset_ai_draft(session["teacher_id"])
    return jsonify({"success": True, "message": "New session started. What is the subject?"})


@app.route("/api/ai-assistant/message", methods=["POST"])
def ai_assistant_message():
    if require_login():
        return jsonify({"success": False, "message": "Please login again."}), 401

    teacher_id = session["teacher_id"]
    text = (request.json or {}).get("message", "").strip()
    if not text:
        return jsonify({"success": False, "message": "Please type a message."}), 400

    lower = text.lower().strip()
    draft = get_ai_draft(teacher_id)

    if lower in {"hi", "hello", "hey", "start", "restart", "new"}:
        reset_ai_draft(teacher_id)
        return jsonify({"success": True, "message": "👋 Hello! I am your AI Question Bank Assistant. I will guide you step by step and save only the questions you choose. What subject are we working on?"})

    if lower in {"thanks", "thank you", "thankyou", "thx"}:
        return jsonify({"success": True, "message": "😊 You're very welcome! Whenever you are ready, start a new question set."})

    if lower in {"bye", "goodbye", "see you"}:
        return jsonify({"success": True, "message": "👋 Goodbye! Your saved questions will stay in your Question Bank."})

    if lower in {"how are you", "how are you?"}:
        return jsonify({"success": True, "message": "😊 I’m doing great and ready to prepare your question set!"})

    if lower in {"cancel", "reset", "stop"}:
        reset_ai_draft(teacher_id)
        return jsonify({"success": True, "message": "Cancelled. Let's start again. What subject are we working on?"})

    if not draft or not draft["subject"]:
        upsert_ai_draft(teacher_id, subject=text, status="collecting")
        return jsonify({"success": True, "message": "Perfect! 📚 Now upload the syllabus file using the 📎 Syllabus File button."})

    if not draft["syllabus_text"]:
        return jsonify({"success": True, "message": "Please upload the syllabus file first using the 📎 Syllabus File button."})

    if not draft["question_type"]:
        if "mcq" in lower:
            qtype = "MCQ"
        elif "short" in lower:
            qtype = "Short Answer"
        elif "long" in lower:
            qtype = "Long Answer"
        else:
            return jsonify({"success": True, "message": "Please choose **MCQ**, **Short Answer**, or **Long Answer**."})
        upsert_ai_draft(teacher_id, question_type=qtype)
        return jsonify({"success": True, "message": "Great choice! How many questions should I prepare? You can choose 1–20."})

    if not draft["count"]:
        match = re.search(r"\b([1-9][0-9]?)\b", text)
        if not match:
            return jsonify({"success": True, "message": "Please enter the number of questions, for example **10**."})
        count = int(match.group(1))
        if count > 20:
            return jsonify({"success": True, "message": "For one set, please choose 20 or fewer questions."})
        upsert_ai_draft(teacher_id, count=count)
        return jsonify({"success": True, "message": "Perfect. What difficulty level do you want: **Easy**, **Medium**, **Hard**, or **Mixed**?"})

    if not draft["difficulty"]:
        if "easy" in lower:
            difficulty = "Easy"
        elif "medium" in lower:
            difficulty = "Medium"
        elif "hard" in lower:
            difficulty = "Hard"
        elif "mixed" in lower:
            difficulty = "Mixed"
        else:
            return jsonify({"success": True, "message": "Please choose **Easy**, **Medium**, **Hard**, or **Mixed**."})
        upsert_ai_draft(teacher_id, difficulty=difficulty)
        return jsonify({"success": True, "message": "Everything is ready. Type **generate** and I’ll prepare the questions for you. I’ll show them first, then you can choose which ones to save."})

    draft = get_ai_draft(teacher_id)

    # Review mode: generated questions are shown first and are NOT saved yet.
    questions = []
    if draft and draft["questions_json"]:
        try:
            questions = json.loads(draft["questions_json"])
        except (TypeError, ValueError):
            questions = []

    if questions:
        regen = re.fullmatch(r"(?:regenerate|regen)\s+(\d+)", lower)
        if regen:
            number = int(regen.group(1))
            if not 1 <= number <= len(questions):
                return jsonify({"success": True, "message": f"Please choose a question number from 1 to {len(questions)}."})
            replacement = generate_question_bank_questions(
                draft["syllabus_text"], draft["subject"], draft["question_type"], 1, draft["difficulty"]
            )
            if not replacement:
                return jsonify({"success": False, "message": "I could not regenerate that question. Please try again."})
            questions[number - 1] = replacement[0]
            upsert_ai_draft(teacher_id, questions_json=json.dumps(questions), status="review")
            return jsonify({"success": True, "message": format_review_questions(questions)})

        # Save all / selected questions only after the teacher confirms.
        if lower in {"all", "add all", "save all", "all questions"}:
            saved_count = save_ai_questions(teacher_id, draft["subject"], draft["question_type"], questions)
            reset_ai_draft(teacher_id)
            return jsonify({"success": True, "message": f"✅ Saved all {saved_count} new questions to the **{draft['subject']}** Question Bank."})

        nums = re.findall(r"\d+", text)
        if nums and (lower.startswith("add") or lower.startswith("save") or "," in text or len(nums) > 0):
            selected = sorted(set(int(n) for n in nums))
            if any(n < 1 or n > len(questions) for n in selected):
                return jsonify({"success": True, "message": f"Please choose question numbers from 1 to {len(questions)}."})
            chosen = [questions[n - 1] for n in selected]
            saved_count = save_ai_questions(teacher_id, draft["subject"], draft["question_type"], chosen)
            reset_ai_draft(teacher_id)
            return jsonify({"success": True, "message": f"✅ Saved {saved_count} selected question(s) to the **{draft['subject']}** Question Bank."})

        return jsonify({"success": True, "message": "Please type **all** to save every question, **1, 3, 5** (or **add 1, 3, 5**) to save selected questions, or **regenerate 2** to replace question 2."})

    if lower in {"generate", "generate questions", "go", "yes"}:
        questions = generate_question_bank_questions(
            draft["syllabus_text"], draft["subject"], draft["question_type"], draft["count"], draft["difficulty"]
        )
        if not questions:
            return jsonify({"success": False, "message": "❌ I could not generate valid questions from the uploaded syllabus. Check the API key and syllabus, then type **generate** to retry."})
        # Save immediately so successful generation always populates the teacher's Question Bank.
        saved_count = save_ai_questions(teacher_id, draft["subject"], draft["question_type"], questions)
        subject_saved = draft["subject"]
        reset_ai_draft(teacher_id)
        preview = format_generated_questions(questions)
        return jsonify({
            "success": True,
            "message": f"✅ Generated {len(questions)} questions for **{subject_saved}** and saved {saved_count} new question(s) to the Question Bank. Existing duplicates are skipped.",
            "review": preview
        })

    return jsonify({"success": True, "message": assistant_reply_for_state(draft)})

def format_generated_questions(questions):
    """Render a safe, compact preview after the questions have been saved."""
    parts = ["<strong>Generated and saved questions</strong><br><br>"]
    for i, q in enumerate(questions, 1):
        question = html.escape(str(q.get("question", ""))).replace("\\r\\n", "\\n").replace("\\n", "<br>")
        parts.append(f"<div class='review-question'><b>Q{i}. {question}</b>")
        if q.get("options"):
            for letter, option in zip("ABCD", q["options"]):
                parts.append(f"<br>{letter}) {html.escape(str(option))}")
        if q.get("chart_image"):
            image = str(q["chart_image"]).replace("\\\\", "/")
            parts.append(f"<br><img src='/static/{image}' style='max-width:520px;border:1px solid #ddd;border-radius:8px;padding:6px;background:#fff'>")
        parts.append(f"<br><small>Marks: {q.get('marks', 1)} | Difficulty: {q.get('difficulty','Medium')}</small></div><br>")
    return "".join(parts)


def format_review_questions(questions):
    parts = ["<strong>Generated Questions</strong><br><br>"]
    for i, q in enumerate(questions, 1):
        parts.append(f"<div class='review-question'><b>Q{i}. {q['question']}</b>")
        if q.get("options"):
            for letter, option in zip("ABCD", q["options"]):
                parts.append(f"<br>{letter}) {option}")
        if q.get("chart_image"):
            image = q["chart_image"].replace("\\", "/")
            parts.append(f"<br><img src='/static/{image}' style='max-width:520px;border:1px solid #ddd;border-radius:8px;padding:6px;background:#fff'>")
        parts.append(f"<br><small>Marks: {q.get('marks', 1)} | Difficulty: {q.get('difficulty','Medium')}</small></div>")
    parts.append("<br><b>What would you like to save?</b> Type <b>all</b> to add every question, <b>1, 3, 5</b> (or <b>add 1, 3, 5</b>) to add selected questions, or <b>regenerate 2</b> to replace question 2.")
    return "".join(parts)


@app.route("/api/ai-assistant/upload-syllabus", methods=["POST"])
def ai_assistant_upload_syllabus():
    if require_login():
        return jsonify({"success": False, "message": "Please login again."}), 401

    syllabus_file = request.files.get("syllabus")
    if not syllabus_file or not syllabus_file.filename:
        return jsonify({"success": False, "message": "Please upload a syllabus PDF or image (PNG/JPG/JPEG)."}), 400

    teacher_id = session["teacher_id"]
    draft = get_ai_draft(teacher_id)
    if not draft or not draft["subject"]:
        return jsonify({"success": False, "message": "Please enter the subject first."}), 400

    filename = secure_filename(syllabus_file.filename)
    ext = os.path.splitext(filename)[1].lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg"}
    if ext not in allowed:
        return jsonify({"success": False, "message": "Supported syllabus formats: PDF, PNG, JPG and JPEG."}), 400

    upload_folder = "uploads"
    os.makedirs(upload_folder, exist_ok=True)
    saved_path = os.path.join(upload_folder, f"ai_{teacher_id}_{uuid.uuid4().hex[:8]}_{filename}")
    syllabus_file.save(saved_path)

    try:
        if ext == ".pdf":
            syllabus_text = extract_pdf_text(saved_path)
            if not syllabus_text or not syllabus_text.strip():
                # Some PDFs are scanned images. Send the PDF itself to Gemini so it can read the visual content.
                if not client:
                    return jsonify({"success": False, "message": "The PDF contains no readable text and AI vision is unavailable."}), 400
                with open(saved_path, "rb") as f:
                    pdf_part = genai.types.Part.from_bytes(data=f.read(), mime_type="application/pdf") if hasattr(genai, "types") else None
                if pdf_part is None:
                    from google.genai import types
                    with open(saved_path, "rb") as f:
                        pdf_part = types.Part.from_bytes(data=f.read(), mime_type="application/pdf")
                vision_prompt = """Read this syllabus document carefully. Extract and preserve all syllabus content, including mathematical equations, symbols, formulas, sequences, notation, code-like examples, unit numbers, headings and bullet points. Return a clean plain-text representation that keeps equations readable. Do not invent or add content."""
                response = client.models.generate_content(model="gemini-2.5-flash", contents=[vision_prompt, pdf_part])
                syllabus_text = (response.text or "").strip()
        else:
            if not client:
                return jsonify({"success": False, "message": "AI vision is unavailable. Please configure the API key."}), 400
            from google.genai import types
            with open(saved_path, "rb") as f:
                image_part = types.Part.from_bytes(data=f.read(), mime_type=syllabus_file.mimetype or ("image/png" if ext == ".png" else "image/jpeg"))
            vision_prompt = """Read this syllabus image as a university question-paper expert. Extract all visible syllabus content accurately. Preserve mathematical equations, symbols, formulas, Fibonacci/series notation, operators, matrices, Greek letters, unit headings and bullet points. Convert visual notation into readable text (for example F(n)=F(n-1)+F(n-2)). Do not invent topics. Return only the structured syllabus text."""
            response = client.models.generate_content(model="gemini-2.5-flash", contents=[vision_prompt, image_part])
            syllabus_text = (response.text or "").strip()
    except Exception as e:
        return jsonify({"success": False, "message": f"Could not read the syllabus: {e}"}), 400

    if not syllabus_text.strip():
        return jsonify({"success": False, "message": "The syllabus could not be read. Please upload a clearer PDF/image."}), 400

    upsert_ai_draft(teacher_id, syllabus_text=syllabus_text, status="collecting")
    label = "image" if ext in {".png", ".jpg", ".jpeg"} else "PDF"
    return jsonify({"success": True, "message": f"✅ Syllabus {label} uploaded and understood successfully. Mathematical formulas and symbols are preserved. Which question type do you want: **MCQ**, **Short Answer**, or **Long Answer**?"})

#test
@app.route("/test_ai_agent")
def test_ai_agent():
    return render_template("test_ai_agent.html")

# ================= RUN =================
if __name__ == "__main__":
    app.run(
        debug=True,
        use_reloader=False,
        threaded=True
    )