Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Check if server is already running on port 8080
isRunning = False
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 2000, 2000, 2000, 2000
http.Open "GET", "http://127.0.0.1:8080/api/status", False
http.Send ""

If Err.Number = 0 And http.Status = 200 Then
    isRunning = True
End If
Err.Clear
On Error GoTo 0

' If not running, start server silently in the background (0 = hidden window)
If Not isRunning Then
    WshShell.CurrentDirectory = "C:\Nifty"
    WshShell.Run "cmd /c python server.py", 0, False
    WScript.Sleep 2500
End If

' Automatically open localhost dashboard in default browser
WshShell.Run "http://127.0.0.1:8080"
