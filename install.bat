@echo off
rem Install wb_toy_link into a WorldboxAI checkout.
rem   install.bat C:\path\to\WorldboxAI          (directory junction)
rem   install.bat C:\path\to\WorldboxAI --copy   (plain copy)

set TARGET=%~1
set MODE=%~2
set SRC=%~dp0wb_toy_link

if "%TARGET%"=="" goto usage
if not exist "%TARGET%\modules" goto usage

set DEST=%TARGET%\modules\wb_toy_link
if exist "%DEST%" rmdir /s /q "%DEST%"

if "%MODE%"=="--copy" (
    xcopy /e /i /q "%SRC%" "%DEST%" >nul
    echo Copied wb_toy_link to %DEST%
) else (
    mklink /J "%DEST%" "%SRC%"
)
echo Restart the WorldboxAI backend; "Toy Link" appears in the module list
echo and "Toy Studio" in the main menu.
goto :eof

:usage
echo Usage: install.bat C:\path\to\WorldboxAI [--copy]
echo (the target must contain a modules\ directory)
exit /b 1
