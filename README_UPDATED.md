# Smart Question Paper Generator

## Updated features
- Flask-based application.
- Question Bank questions now store a difficulty level: Easy, Medium, or Hard.
- Question Bank generation filters by the selected difficulty and reports when there are not enough matching questions.
- AI generation receives the selected difficulty level.
- Coordinator terminology replaces HOD in the visible interface.
- AI syllabus assistant accepts PDF, PNG, JPG, and JPEG syllabus files.
- Image/scanned syllabus processing uses Gemini vision to preserve equations, formulas, symbols, Fibonacci/series notation, matrices, and other visual notation before question generation.
- Existing databases are migrated automatically to add the Question Bank difficulty column.

## Run
Use Python 3.10 or another supported Python installation with the packages in `requirements.txt`.

```powershell
python -m pip install -r requirements.txt
python app.py
```

If Windows has multiple Python installations, use the Python 3.10 executable that was used for this project.


## New AI Statistics & Difficulty Features
- Statistics-aware AI question generation supports bar charts, pie charts, histograms, line graphs, frequency polygons and ogives when appropriate to the syllabus.
- Graph data is stored with each generated Question Bank entry and the rendered graph is reused in paper preview/PDF.
- Question Bank questions carry Easy, Medium or Hard difficulty metadata.
- Paper generation supports Easy, Medium, Hard and Mixed. Mixed selection intentionally combines the available difficulty levels.
- AI-generated questions are also added to the Question Bank automatically, with duplicate protection.
- Existing older Question Bank entries without a difficulty value are migrated to Medium and can be edited later.

## Statistics graphs and question figures
- Statistics AI prompts request data/chart-based questions when appropriate, using bar charts, pie charts, histograms, line graphs, frequency polygons and ogives. Generated graph data is rendered to PNG images and stored with the question-bank record.
- In Question Bank > open a subject, each question has an **Attach or replace image for this question** control. Select an image and click **Save All Changes** to store it with that question.
- When manually adding questions, each question card supports an optional image upload and optional chart type/data JSON. To generate a chart automatically, choose a chart type and enter valid JSON data, for example `{"labels":["A","B","C"],"values":[25,35,40]}` for a pie or bar chart.
- Accepted image formats: PNG, JPG/JPEG, WEBP and GIF. Uploaded figures are stored under `static/uploads/question_images/`.
- The Profile page sidebar is fixed while the main profile content scrolls.
