@echo off
rem Drag PDFs onto this file to convert them, or double-click to convert every PDF in this folder.
cd /d "%~dp0"
python "%~dp0pdf_to_csv.py" %*
