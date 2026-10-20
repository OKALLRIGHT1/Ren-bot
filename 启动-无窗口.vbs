Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
renExe = root & "\ren-bot.exe"
liveExe = root & "\Live2D-Suzu.exe"
runtimePythonw = root & "\runtime\pythonw.exe"
runtimePython = root & "\runtime\python.exe"
venvPythonw = root & "\.venv\Scripts\pythonw.exe"
venvPython = root & "\.venv\Scripts\python.exe"
launcher = root & "\launcher.py"
startBat = root & "\启动.bat"

shell.CurrentDirectory = root

' 启动 GUI 程序（ren-bot.exe / Live2D-Suzu.exe 自带无黑框，第2个参数传 1 确保进度小窗正常居中显示，避免被 0/SW_HIDE 强行隐藏）
If fso.FileExists(renExe) Then
    shell.Run Chr(34) & renExe & Chr(34), 1, False
ElseIf fso.FileExists(liveExe) Then
    shell.Run Chr(34) & liveExe & Chr(34), 1, False
ElseIf fso.FileExists(runtimePythonw) And fso.FileExists(launcher) Then
    shell.Run Chr(34) & runtimePythonw & Chr(34) & " " & Chr(34) & launcher & Chr(34), 1, False
ElseIf fso.FileExists(venvPythonw) And fso.FileExists(launcher) Then
    shell.Run Chr(34) & venvPythonw & Chr(34) & " " & Chr(34) & launcher & Chr(34), 1, False
ElseIf fso.FileExists(startBat) Then
    shell.Run Chr(34) & startBat & Chr(34), 0, False
Else
    MsgBox "找不到运行环境，请先双击「安装环境.bat」配置环境。", 16, "Ren-bot"
End If
