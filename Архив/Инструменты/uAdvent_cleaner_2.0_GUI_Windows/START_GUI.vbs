Option Explicit
Dim sh, fs, base, command
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
base = fs.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & base & "\advent_gui.ps1"""
On Error Resume Next
sh.Run command, 0, False
If Err.Number <> 0 Then MsgBox "Cannot start PowerShell GUI: " & Err.Description, vbCritical, "uAdvent"
