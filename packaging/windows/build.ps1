# Builds dist\ClassDeck (the app folder) and dist\ClassDeck-Setup.exe (the setup wizard).
# Run from the repository root: powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# Needs: the project venv with the `desktop` extra and pyinstaller, and Inno Setup 6.
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$here = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$dist = Join-Path $root 'dist'

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 is missing: winget install JRSoftware.InnoSetup' }
$version = (Select-String -Path (Join-Path $root 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value

& $python (Join-Path $here 'make_icon.py') (Join-Path $here 'classdeck.ico')
& $python -m PyInstaller (Join-Path $here 'classdeck.spec') --noconfirm `
    --distpath $dist --workpath (Join-Path $root 'build')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

& $iscc /Q "/DAppVersion=$version" (Join-Path $here 'classdeck.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
Get-Item (Join-Path $dist 'ClassDeck-Setup.exe') |
    Select-Object Name, @{ n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }
