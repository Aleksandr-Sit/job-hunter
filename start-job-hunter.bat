@echo off
cd /d "c:\Users\sanny\My Project\job-hunter"
set PYTHONUTF8=1
"C:\Users\sanny\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m src.scheduler >> "data\logs\job-hunter.log" 2>&1
