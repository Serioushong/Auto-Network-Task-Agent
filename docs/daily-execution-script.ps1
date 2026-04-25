Set-Location 'D:\Spec-Plan-Harness'
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\python.exe -m pytest -q
