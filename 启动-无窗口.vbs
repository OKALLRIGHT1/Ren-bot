Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
venvPythonw = root & "\.venv\Scripts\pythonw.exe"
venvPython = root & "\.venv\Scripts\python.exe"
launcher = root & "\launcher.py"
startBat = root & "\启动.bat"

If fso.FileExists(venvPythonw) And fso.FileExists(launcher) Then
    shell.CurrentDirectory = root
    shell.Run """" & venvPythonw & """ """ & launcher & """", 0, False
ElseIf fso.FileExists(venvPython) And fso.FileExists(launcher) Then
    shell.CurrentDirectory = root
    shell.Run """" & venvPython & """ """ & launcher & """", 0, False
ElseIf fso.FileExists(startBat) Then
    shell.Run """" & startBat & """", 1, False
Else
    MsgBox "找不到启动脚本。请先运行「安装环境.bat」。", 16, "Live2D-Suzu"
End If
