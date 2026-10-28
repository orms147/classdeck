# Builds dist\ClassDeck (the app folder) and dist\ClassDeck-Setup.exe (the one-file installer).
# Run from the repository root: powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# Needs: the project venv with the `api` and `desktop` extras and pyinstaller.
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$here = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$dist = Join-Path $root 'dist'
$pkg = Join-Path $dist 'setup-files'

& $python (Join-Path $here 'make_icon.py') (Join-Path $here 'classdeck.ico')
& $python -m PyInstaller (Join-Path $here 'classdeck.spec') --noconfirm `
    --distpath $dist --workpath (Join-Path $root 'build')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

if (Test-Path $pkg) { Remove-Item $pkg -Recurse -Force }
New-Item -ItemType Directory -Force $pkg | Out-Null
Compress-Archive -Path (Join-Path $dist 'ClassDeck\*') -DestinationPath (Join-Path $pkg 'ClassDeck.zip')
Copy-Item (Join-Path $here 'install.ps1'), (Join-Path $here 'uninstall.ps1') $pkg

# IExpress (part of Windows) wraps the files into a self-extracting setup that runs install.ps1
$setup = Join-Path $dist 'ClassDeck-Setup.exe'
$sed = Join-Path $pkg 'setup.sed'
@"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$setup
FriendlyName=ClassDeck
AppLaunched=powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File install.ps1
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[Strings]
FILE0="ClassDeck.zip"
FILE1="install.ps1"
FILE2="uninstall.ps1"
[SourceFiles]
SourceFiles0=$pkg\
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
"@ | Set-Content -Path $sed -Encoding ASCII
& "$env:SystemRoot\System32\iexpress.exe" /N /Q $sed | Out-Null
$deadline = (Get-Date).AddMinutes(10)
while (-not (Test-Path $setup) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
Get-Process makecab -ErrorAction SilentlyContinue | Wait-Process -Timeout 600
Get-Item $setup | Select-Object Name, @{ n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }
