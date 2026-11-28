@echo off
rem Avvia il pannello di sviluppo sugli stessi dati dell'app installata
rem (impostazioni, database, binari gestiti in C:\SW\StreamingCommunity Downloader).
rem Non usare insieme all'app installata: il database non e' pensato per due processi.
rem Uso: scripts\run-dev-panel.cmd   (la porta si cambia con PORT=8001 prima del comando)

setlocal
set "SW=C:\SW\StreamingCommunity Downloader"
set "DATA_FILE=%SW%\data.json"
set "DB_FILE=%SW%\panel.db"

set "PY=C:\SOURCECODE\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

cd /d "%~dp0.."
"%PY%" main.py
