@echo off
REM Wrapper so dev_run.ps1 can be launched from cmd.exe.
REM
REM cmd cannot execute .ps1 directly, so this goes through powershell.
REM Arguments pass through unchanged:  dev_run.bat -LinkOnly
REM
REM In a PowerShell window just call .\scripts\dev_run.ps1 directly.
REM
REM NOTE: comments here are ASCII on purpose. cmd reads .bat files using the
REM console codepage, which varies per machine, so Korean comments get
REM mangled into bogus commands. Every other file in this repo uses Korean.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev_run.ps1" %*
exit /b %errorlevel%
