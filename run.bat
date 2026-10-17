@echo off
rem Launch the Image Forensics Workbench (Qt).
rem Drag an image file onto this script to open it directly.
setlocal
cd /d "%~dp0"
python ifw.py %*
if errorlevel 1 pause
