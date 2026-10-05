import os
import win32com.client

desktop = os.path.join(os.path.join(os.environ['USERPROFILE']), 'Desktop')
shortcut_path = os.path.join(desktop, "NIFTYBOX - Fyers Terminal.lnk")

shell = win32com.client.Dispatch("WScript.Shell")
shortcut = shell.CreateShortCut(shortcut_path)
shortcut.Targetpath = "wscript.exe"
shortcut.Arguments = '"C:\\Nifty\\launch_desktop.vbs"'
shortcut.WorkingDirectory = "C:\\Nifty"
shortcut.IconLocation = "C:\\Windows\\System32\\imageres.dll,114"
shortcut.Description = "Launch NIFTYBOX - Fully Automatic Fyers Terminal"
shortcut.save()

print(f"Desktop shortcut created at: {shortcut_path}")
