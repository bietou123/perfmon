@echo off
rem ASCII-only wrapper: PowerShell 5.1 reads BOM-less scripts as ANSI/GBK,
rem so all non-ASCII text stays in Python/HTML, never in this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
pause
