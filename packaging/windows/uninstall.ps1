# Removes the ClassDeck program, its shortcuts and its Apps entry. The lessons in
# Documents\ClassDeck and the settings in %APPDATA%\ClassDeck are kept.
$ErrorActionPreference = 'SilentlyContinue'
$target = Join-Path $env:LOCALAPPDATA 'Programs\ClassDeck'
Add-Type -AssemblyName System.Windows.Forms
$answer = [System.Windows.Forms.MessageBox]::Show(
    "Remove ClassDeck from this computer?`n`nYour lessons in Documents\ClassDeck are kept.",
    'Uninstall ClassDeck', 'YesNo', 'Question')
if ($answer -ne 'Yes') { exit }

Get-Process ClassDeck | Stop-Process -Force
Start-Sleep -Milliseconds 500
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'ClassDeck.lnk') -Force
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'ClassDeck') -Recurse -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClassDeck' -Recurse -Force
# this script lives in the folder it removes: delete the folder from a short-lived helper
Start-Process cmd.exe -WindowStyle Hidden -ArgumentList "/c timeout /t 2 /nobreak >nul & rmdir /s /q `"$target`""
