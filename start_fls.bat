@echo off
REM Start the Fiber Length Measurement software (V2) with the Anaconda base environment.
REM Add  --simulate  after run.py to try the program without instruments.

call "%USERPROFILE%\anaconda3\Scripts\activate.bat" base
cd /d "%~dp0"
python run.py %*
if errorlevel 1 pause
