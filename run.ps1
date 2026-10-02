param(
    [ValidateSet('init','monitor','dashboard','midpoint','remaining','report','replay','test')]
    [string]$Command = 'report',
    [string]$Python = '',
    [int]$Cycles = 0
)
$ErrorActionPreference = 'Stop'
$pythonArgs = @('-X', 'utf8')
if ([string]::IsNullOrWhiteSpace($Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Python = 'py'
        $pythonArgs = @('-3', '-X', 'utf8')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $Python = 'python'
    } else {
        throw 'Install Python 3.12 or later, or pass -Python with your python.exe path.'
    }
}
if ($Command -ne 'init' -and -not (Test-Path -LiteralPath "$PSScriptRoot\config.json")) {
    throw 'Initialize this copy first: .\run.ps1 init'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    switch ($Command) {
        'init'      { & $Python @pythonArgs 'init.py' }
        'monitor'   { & $Python @pythonArgs 'bot.py' 'monitor' '--cycles' $Cycles }
        'dashboard' { & $Python @pythonArgs 'dashboard.py' }
        'midpoint'  { & $Python @pythonArgs 'midpoint_call.py' 'monitor' }
        'remaining' { & $Python @pythonArgs 'remaining_calls.py' 'monitor' }
        'report'    { & $Python @pythonArgs 'bot.py' 'report' }
        'replay'    { & $Python @pythonArgs 'paper_decision.py' }
        'test'      { & $Python @pythonArgs '-m' 'unittest' 'discover' '-s' '.' '-p' 'test_*.py' }
    }
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
