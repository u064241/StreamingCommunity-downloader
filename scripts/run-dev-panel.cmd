@echo off
rem Avvia il pannello di sviluppo dal repo sulla cartella dati standard:
rem %LOCALAPPDATA%\StreamingCommunityDownloader, la stessa usata dall'app installata.
rem Impostazioni, database e binari gestiti sono condivisi con l'app installata.
rem Non usare insieme all'app installata: il database non e' pensato per due processi.
rem Uso: scripts\run-dev-panel.cmd   (la porta si cambia con PORT=8001 prima del comando)

setlocal
set "PY=C:\SOURCECODE\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

cd /d "%~dp0.."
"%PY%" main.py
