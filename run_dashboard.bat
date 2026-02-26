@echo off
echo Starting Trading Brain Dashboard...
call .\.venv\Scripts\activate.bat
streamlit run dashboard.py
pause
