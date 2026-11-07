@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"

rem ---- first launch: restart myself inside a MAXIMIZED console window ----
rem      so the intro splash can fill the screen; launcher restores the size right after.
if /i "%~1"=="--maxrun" goto run
start "Fortnite Auto Bot" /max cmd /c ""%~f0" --maxrun %*"
exit /b 0

:run
title Fortnite Auto Bot ^| QQ 2395019665
set "PYRUN="

if exist "%HERE%python\python.exe" set PYRUN="%HERE%python\python.exe"
if not defined PYRUN (
    where py >nul 2>nul
    if not errorlevel 1 set "PYRUN=py -3"
)
if not defined PYRUN (
    where python >nul 2>nul
    if not errorlevel 1 set "PYRUN=python"
)
if not defined PYRUN goto nopython

echo.
echo   TIP: first time? press [4] + Enter to set YOUR in-game name.
echo        (the bot finds you by your name at the bottom-left of the screen)
echo.
%PYRUN% "%HERE%launcher.py" %*

if /i "%~2"=="--demo" exit /b 0
echo.
echo   [bot exited] double-click "QiDong.bat" again to restart.
echo.
pause
exit /b 0

:nopython
echo.
echo   [!] Python runtime not found.
echo       Keep the "python" folder in the SAME directory as this file
echo       (copy the whole folder, do not move single files out).
echo.
echo       Still broken? Contact QQ 2395019665
echo.
pause
exit /b 1