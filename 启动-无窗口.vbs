Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
exe = root & "\dist\launcher\Live2D-Suzu.exe"
If CreateObject("Scripting.FileSystemObject").FileExists(exe) Then
    shell.Run """" & exe & """", 0, False
Else
    shell.Run "powershell.exe -ExecutionPolicy Bypass -File """ & root & "\scripts\build_launcher.ps1""", 1, True
    If CreateObject("Scripting.FileSystemObject").FileExists(exe) Then
        shell.Run """" & exe & """", 0, False
    Else
        MsgBox "启动器构建失败，请手动运行 scripts\build_launcher.ps1 查看错误。", 16, "Live2D-Suzu"
    End If
End If
