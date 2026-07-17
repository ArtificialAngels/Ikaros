' See docs/scripts/bin/launch-hidden.md
If WScript.Arguments.Count < 1 Then
    WScript.Echo "Usage: wscript.exe launch-hidden.vbs ""command"""
    WScript.Quit 1
End If
Set shell = CreateObject("WScript.Shell")
shell.Run WScript.Arguments(0), 0, False
