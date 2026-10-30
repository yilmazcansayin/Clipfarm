' Clipfarm yardimci uygulamasini pencere acmadan baslatir.
' Ilk calistirmada (kurulum yoksa) kurulum icin baslat.bat'i acar.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = dir & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(pyw) Then
  sh.CurrentDirectory = dir
  sh.Run """" & pyw & """ -m clipfarm serve", 0, False
Else
  sh.Run """" & dir & "\baslat.bat""", 1, False
End If
