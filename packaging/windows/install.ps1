# Installs ClassDeck for the current user (no administrator rights): run by ClassDeck-Setup.exe
# from the folder it unpacks to, next to ClassDeck.zip and uninstall.ps1.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $env:LOCALAPPDATA 'Programs\ClassDeck'
$exe = Join-Path $target 'ClassDeck.exe'
$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'ClassDeck'
$desktop = [Environment]::GetFolderPath('Desktop')

Get-Process ClassDeck -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
New-Item -ItemType Directory -Force $target | Out-Null
Expand-Archive -Path (Join-Path $here 'ClassDeck.zip') -DestinationPath $target -Force
Copy-Item (Join-Path $here 'uninstall.ps1') $target -Force

function New-Shortcut([string]$path, [string]$file, [string]$arguments, [string]$icon) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $file
    $link.Arguments = $arguments
    $link.WorkingDirectory = $target
    $link.IconLocation = $icon
    $link.Save()
}

New-Item -ItemType Directory -Force $menu | Out-Null
New-Shortcut (Join-Path $desktop 'ClassDeck.lnk') $exe '' "$exe,0"
New-Shortcut (Join-Path $menu 'ClassDeck.lnk') $exe '' "$exe,0"
$uninstall = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$target\uninstall.ps1`""
New-Shortcut (Join-Path $menu 'Uninstall ClassDeck.lnk') 'powershell.exe' $uninstall "$exe,0"

# listed in Settings > Apps for this user
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClassDeck'
New-Item -Path $key -Force | Out-Null
$values = @{
    DisplayName = 'ClassDeck'; DisplayIcon = $exe; InstallLocation = $target
    Publisher = 'ClassDeck'; DisplayVersion = '0.1.0'
    UninstallString = "powershell.exe $uninstall"
}
foreach ($name in $values.Keys) { Set-ItemProperty -Path $key -Name $name -Value $values[$name] }
Set-ItemProperty -Path $key -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name NoRepair -Value 1 -Type DWord

Start-Process $exe
