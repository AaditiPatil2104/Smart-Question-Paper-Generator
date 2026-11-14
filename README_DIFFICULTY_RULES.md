# Smart Question Paper Generator – Difficulty-Aware Question Rules

## Updated behavior
The generator now applies difficulty rules across **all subjects**, not only IoT.

### Easy
- MCQ: short, direct, basic concept questions.
- Short Notes: topic/concept names or very short phrases (for example: `Raspberry Pi`, `Advantages of IoT`, `Sensors`).
- Long Answer: one simple, single-focus question, targeted to about 100 characters / 1–1.5 printed lines.
- Avoids case studies, surveys, multi-topic questions and unnecessary background wording.
- A/B split questions use short companion prompts instead of long essay-style prompts.

### Medium
- Moderately descriptive university questions.
- Short Notes remain concise.
- Long answers can use 1–3 sentences when needed.

### Hard / Difficult
- Analytical, comparative, application-based and multi-step questions are allowed.
- Short Notes remain concise even at Hard level.

### Mixed
Questions are distributed across Easy, Medium and Hard and retain their difficulty in the Question Bank.

## Question Bank
Questions retain:
- Subject
- Question type
- Difficulty
- Marks
- Chart/graph metadata where applicable

Paper generation filters the Question Bank by the selected difficulty. `Mixed` selects a mixture of levels.

## AI generation
The AI prompt now explicitly enforces the same style rules. AI-generated questions are automatically saved to the Question Bank with their assigned difficulty.

## Statistics support
The existing graph support remains available for subjects/syllabus content involving:
- Bar charts
- Pie charts
- Histograms
- Line graphs
- Frequency polygons
- Ogives
- Mathematical symbols/formulas where appropriate

These rules are subject-independent: IoT, Java, Python, DBMS, Computer Networks, Statistics, etc. use the same difficulty framework while the AI adapts the content to the selected syllabus.
