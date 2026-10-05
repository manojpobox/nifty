@echo off
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
copy /y "C:\Nifty\start_niftybox.bat" "%STARTUP_FOLDER%\NIFTYBOX.bat"
echo [OK] NIFTYBOX added to Windows Startup. It will now run automatically on PC boot.
