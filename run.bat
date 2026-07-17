@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 goto :python_missing

if /i "%ACCOUNTINGDEMO_SMOKE_TEST%"=="1" goto :smoke_test

python main.py
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" goto :app_failed
exit /b 0

:smoke_test
python -c "import main; print('Small Enterprise Accounting launcher OK')"
exit /b %ERRORLEVEL%

:python_missing
echo Python was not found in PATH.
echo Install Python and run: pip install -r requirements.txt
pause
exit /b 1

:app_failed
echo.
echo Small Enterprise Accounting failed with exit code %APP_EXIT_CODE%.
echo Run: pip install -r requirements.txt
echo Then check config.json and retry.
pause
exit /b %APP_EXIT_CODE%
