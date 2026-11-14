# Question Paper Generator – fixes

## What was fixed
- The AI Question Bank Assistant now saves generated questions to the logged-in teacher's Question Bank immediately after successful generation.
- The assistant displays a save confirmation and a preview. Existing identical questions are skipped to avoid duplicates.
- Statistics prompts now require datasets to be presented as clear columnar tables, with suitable columns such as class interval, frequency and cumulative frequency.
- Questions that ask students to draw/construct/plot a graph retain the source dataset but do not show a finished chart image. Rendered charts are reserved for questions that ask students to read or interpret an already drawn chart.
- Short-answer questions are prompted to be focused, syllabus-aligned, complete, and supplied with a correct concise model answer.
- The embedded API key was removed from source. Configure your own `API_KEY` environment variable before running the app.

## Configure Gemini API key on Windows PowerShell
In the same terminal you use to run Flask:
```powershell
$env:API_KEY="YOUR_GEMINI_API_KEY"
python app.py
```
Use your own key from Google AI Studio. Do not put a real key in a shared ZIP or source-control repository.

If the project already has a Python virtual environment, activate it first and install dependencies:
```powershell
python -m pip install -r requirements.txt
```

The existing `database.db` is included to preserve the project's current data. Back it up before testing changes.
