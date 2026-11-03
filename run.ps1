param([switch]$Check)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $localPython)) {
    $candidates = @()
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $candidates += Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $usablePython = $null
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            try {
                $null = & $candidate --version 2>&1
                if ($LASTEXITCODE -eq 0) { $usablePython = $candidate; break }
            } catch { }
        }
    }
    if (-not $usablePython) { throw 'Install Python 3.12 from python.org, then run this script again.' }
    & $usablePython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project virtual environment.' }
}
& $localPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the network and retry.' }
if ($Check) {
    & $localPython -m pytest -q
} else {
    & $localPython -m streamlit run app.py
}
exit $LASTEXITCODE
