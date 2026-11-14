@echo off
set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not exist "%OLLAMA_EXE%" (
  echo Ollama was not found at "%OLLAMA_EXE%". Install it from https://ollama.com/download/windows
  pause
  exit /b 1
)
"%OLLAMA_EXE%" run qwen2.5:3b
pause
