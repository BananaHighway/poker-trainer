# Windows launcher. No args: start the app at http://127.0.0.1:8765
# With args: run a script in the venv, e.g.  .\run.ps1 scripts\simulate.py 500 6
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if ($args.Count -eq 0) {
    Write-Host "Poker Trainer -> http://127.0.0.1:8765"
    & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --app-dir $PSScriptRoot
} else {
    & $py @args
}
