@echo off
cd /d "%~dp0"
echo ==== %date% %time% ==== >> maj.log
"%LOCALAPPDATA%\Python\bin\python.exe" generer_marche.py --push >> maj.log 2>&1
