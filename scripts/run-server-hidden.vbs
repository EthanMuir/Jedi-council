' Launches run-server.bat with no visible console window -- what Task
' Scheduler's "At log on" trigger should point to, so the server starts
' silently in the background instead of popping up a terminal every login.
Set objShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
objShell.Run """" & scriptDir & "\run-server.bat""", 0, False
