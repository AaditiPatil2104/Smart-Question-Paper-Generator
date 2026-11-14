# Local AI setup for Smart Question Paper Generator

## Why this change
- Gemini remains the first choice when configured and available.
- When Gemini is missing, rate-limited, or errors, the app tries local Ollama.
- If AI is unavailable, paper generation tries to use matching saved questions from the teacher's Question Bank instead of silently creating an empty section.
- Question prompts now ask for distinct, meaningful section questions, coherent (a)/(b)/(c) subparts for long answers, and column-based datasets for Statistics.

## One-time Windows setup
1. Install Ollama from https://ollama.com/download/windows
2. Open Command Prompt and run:
   `"%LOCALAPPDATA%\\Programs\\Ollama\\ollama.exe" pull qwen2.5:3b`
3. Start the Ollama app from the Start menu. Keep it running while using the project.
4. Verify the local API responds:
   `curl http://localhost:11434/api/tags`
5. Start the Flask app using the same Python environment and command you normally use.

The default model is `qwen2.5:3b`. You can override it using environment variables:
- `OLLAMA_MODEL` (default `qwen2.5:3b`)
- `OLLAMA_URL` (default `http://localhost:11434/api/generate`)

## Important limitations
- Local model generation does not use Gemini's API quota, but it needs enough RAM, disk space, and time.
- If both Gemini and Ollama are unavailable, a complete paper can only be assembled if the Question Bank contains enough matching questions for the requested subject and types.
- This update preserves the existing database and app structure. Make a backup of `database.db` before replacing an existing project folder.
