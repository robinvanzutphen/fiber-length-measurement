@echo off
REM ===========================================================================
REM  Build the FLS V2 executable and the distribution zip.
REM    build_exe.bat                 uses Anaconda / py launcher / python on PATH
REM    build_exe.bat C:\path\python.exe   uses that Python (3.11 or newer)
REM  Result: dist\FLS\FLS.exe and dist\FLS_V2_<version>.zip
REM  The build uses its own virtual environment (.build-venv); your Python
REM  installation is not modified.
REM ===========================================================================
setlocal
cd /d "%~dp0"

set "PY=%~1"
if "%PY%"=="" if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if "%PY%"=="" (py -3 --version >nul 2>&1 && set "PY=py -3")
if "%PY%"=="" set "PY=python"
echo Using Python: %PY%

if not exist .build-venv\Scripts\python.exe (
    echo Creating build environment ...
    %PY% -m venv .build-venv || goto :error
)
set "VPY=.build-venv\Scripts\python.exe"
REM Anaconda keeps DLLs (OpenSSL, libffi, sqlite, ...) in Library\bin: let PyInstaller find them
for /f "delims=" %%p in ('%VPY% -c "import sys; print(sys.base_prefix)"') do set "BASEPY=%%p"
if exist "%BASEPY%\Library\bin" set "PATH=%BASEPY%\Library\bin;%PATH%"
%VPY% -m pip install --upgrade pip -q || goto :error
%VPY% -m pip install -r requirements.txt pyinstaller -q || goto :error

for /f "delims=" %%v in ('%VPY% -c "import fls; print(fls.__version__)"') do set "VERSION=%%v"
echo Building FLS %VERSION% ...

%VPY% -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\fls.spec || goto :error

echo Copying source code and documentation ...
robocopy . dist\FLS\source /E /NFL /NDL /NJH /NJS /XD .build-venv build dist .git __pycache__ .pytest_cache >nul
if errorlevel 8 goto :error
copy /y packaging\INSTALL.txt dist\FLS\INSTALL.txt >nul
copy /y LICENSE dist\FLS\LICENSE.txt >nul
%VPY% packaging\collect_licenses.py dist\FLS\THIRD_PARTY_LICENSES.txt || goto :error

echo Creating zip ...
if exist "dist\FLS_V2_%VERSION%.zip" del "dist\FLS_V2_%VERSION%.zip"
REM give a virus scanner time to release the freshly written files
ping -n 6 127.0.0.1 >nul
tar -a -c -f "dist\FLS_V2_%VERSION%.zip" -C dist FLS
if errorlevel 1 goto :error

echo.
echo Done:  dist\FLS\FLS.exe
echo        dist\FLS_V2_%VERSION%.zip
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
exit /b 1
