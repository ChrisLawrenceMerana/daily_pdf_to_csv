@echo off
rem Double-click to convert every PDF in this folder to CSV.
cd /d "%~dp0"
python "%~dp0pdf_to_csv.py" --all
