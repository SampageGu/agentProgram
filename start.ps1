[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("chat", "run", "resume", "status", "replay", "security", "explore", "web", "webdemo", "demo", "demo2", "ask", "test", "eval", "eval1", "eval3", "trace", "help")]
    [string]$Mode = "chat",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$launcher = Join-Path $projectRoot "run_mini_code.py"
$envFile = Join-Path $projectRoot ".env"

function Resolve-Python {
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $systemPython) {
        Write-Warning ".venv was not found; using system Python. Recommended: python -m venv .venv"
        return $systemPython.Source
    }

    throw "Python was not found. Install Python 3.11+ or create .venv in the project root."
}

function Show-Help {
    Write-Host "MiniCode 0.9 launcher (M4.2 + M5.2)"
    Write-Host ""
    Write-Host "  .\start.cmd                         Start multi-turn chat"
    Write-Host "  .\start.cmd chat                    Start multi-turn chat"
    Write-Host "  .\start.cmd run `"coding task`"      Run a checkpointed coding task"
    Write-Host "  .\start.cmd resume <run_id>         Resume an interrupted M2 run"
    Write-Host "  .\start.cmd status <run_id>         Show persisted run status"
    Write-Host "  .\start.cmd replay <run_id>         Replay timeline and metrics"
    Write-Host "  .\start.cmd security                Run offline security probes"
    Write-Host "  .\start.cmd explore 'question'     Run the M5.1 Explore Subagent"
    Write-Host "  .\start.cmd web                     Start M4.2 at http://127.0.0.1:8765"
    Write-Host "  .\start.cmd webdemo                 Start M4.2 with a disposable workspace"
    Write-Host "  .\start.cmd demo                    Run the disposable M1 demo"
    Write-Host "  .\start.cmd demo2                   Create a resumable M2 demo"
    Write-Host "  .\start.cmd ask `"your question`"   Ask directly"
    Write-Host "  .\start.cmd test                    Run offline tests"
    Write-Host "  .\start.cmd eval                    Run 5 DeepSeek eval cases"
    Write-Host "  .\start.cmd eval1                   Run 3 M1 coding eval cases"
    Write-Host "  .\start.cmd eval3 [limit]           Run up to 20 M3 coding cases"
    Write-Host "  .\start.cmd trace <run_id>          Show a saved trace"
    Write-Host "  .\start.cmd help                    Show this help"
}

function Assert-EnvFile {
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw ".env was not found. Run: Copy-Item .env.example .env, then fill DEEPSEEK_API_KEY."
    }
}

$python = Resolve-Python
$commandExitCode = 0

switch ($Mode) {
    "help" {
        Show-Help
    }
    "test" {
        & $python -m unittest discover -s (Join-Path $projectRoot "tests") -v
        $commandExitCode = $LASTEXITCODE
    }
    "eval" {
        Assert-EnvFile
        & $python $launcher eval-m0 --workspace $projectRoot --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
    "eval1" {
        Assert-EnvFile
        & $python $launcher eval-m1 --workspace $projectRoot --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
    "eval3" {
        Assert-EnvFile
        if ($RemainingArgs.Count -ge 1) {
            & $python $launcher eval-m3 --workspace $projectRoot --env-file $envFile --limit $RemainingArgs[0]
        }
        else {
            & $python $launcher eval-m3 --workspace $projectRoot --env-file $envFile
        }
        $commandExitCode = $LASTEXITCODE
    }
    "trace" {
        if ($RemainingArgs.Count -lt 1) {
            throw "Run ID is required. Example: .\start.cmd trace abc123"
        }
        if ($RemainingArgs.Count -ge 2) {
            $traceWorkspace = $RemainingArgs[1]
        }
        else {
            $traceWorkspace = $projectRoot
        }
        & $python $launcher trace $RemainingArgs[0] --workspace $traceWorkspace
        $commandExitCode = $LASTEXITCODE
    }
    "chat" {
        Assert-EnvFile
        & $python $launcher chat --workspace $projectRoot --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
    "run" {
        Assert-EnvFile
        if ($RemainingArgs.Count -lt 1) {
            throw "Coding task is required. Example: .\start.cmd run `"Fix the bug and add tests`""
        }
        $task = $RemainingArgs -join " "
        & $python $launcher run $task --workspace $projectRoot --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
    "resume" {
        Assert-EnvFile
        if ($RemainingArgs.Count -lt 1) {
            throw "Run ID is required. Example: .\start.cmd resume abc123"
        }
        if ($RemainingArgs.Count -ge 2) {
            $resumeWorkspace = $RemainingArgs[1]
        }
        else {
            $resumeWorkspace = $projectRoot
        }
        & $python $launcher resume $RemainingArgs[0] --workspace $resumeWorkspace --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
    "status" {
        if ($RemainingArgs.Count -lt 1) {
            throw "Run ID is required. Example: .\start.cmd status abc123"
        }
        if ($RemainingArgs.Count -ge 2) {
            $statusWorkspace = $RemainingArgs[1]
        }
        else {
            $statusWorkspace = $projectRoot
        }
        & $python $launcher status $RemainingArgs[0] --workspace $statusWorkspace
        $commandExitCode = $LASTEXITCODE
    }
    "replay" {
        if ($RemainingArgs.Count -lt 1) {
            throw "Run ID is required. Example: .\start.cmd replay abc123"
        }
        if ($RemainingArgs.Count -ge 2) {
            $replayWorkspace = $RemainingArgs[1]
        }
        else {
            $replayWorkspace = $projectRoot
        }
        & $python $launcher replay $RemainingArgs[0] --workspace $replayWorkspace
        $commandExitCode = $LASTEXITCODE
    }
    "security" {
        & $python $launcher security-check --workspace $projectRoot
        $commandExitCode = $LASTEXITCODE
    }
    "explore" {
        Assert-EnvFile
        if ($RemainingArgs.Count -lt 1) {
            throw "Exploration question is required."
        }
        $question = $RemainingArgs -join " "
        & $python $launcher explore $question --workspace $projectRoot --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
    "web" {
        Assert-EnvFile
        & $python $launcher web --workspace $projectRoot --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
    "webdemo" {
        Assert-EnvFile
        & $python $launcher web --workspace $projectRoot --env-file $envFile --demo
        $commandExitCode = $LASTEXITCODE
    }
    "demo" {
        Assert-EnvFile
        & $python $launcher demo-m1 --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
    "demo2" {
        Assert-EnvFile
        $previousContextChars = $env:MINICODE_MAX_CONTEXT_CHARS
        $previousArtifactThreshold = $env:MINICODE_ARTIFACT_THRESHOLD
        try {
            $env:MINICODE_MAX_CONTEXT_CHARS = "3500"
            $env:MINICODE_ARTIFACT_THRESHOLD = "800"
            & $python $launcher demo-m1 --env-file $envFile --max-steps 6 --expect-pause
            $commandExitCode = $LASTEXITCODE
        }
        finally {
            if ($null -eq $previousContextChars) {
                Remove-Item Env:MINICODE_MAX_CONTEXT_CHARS -ErrorAction SilentlyContinue
            }
            else {
                $env:MINICODE_MAX_CONTEXT_CHARS = $previousContextChars
            }
            if ($null -eq $previousArtifactThreshold) {
                Remove-Item Env:MINICODE_ARTIFACT_THRESHOLD -ErrorAction SilentlyContinue
            }
            else {
                $env:MINICODE_ARTIFACT_THRESHOLD = $previousArtifactThreshold
            }
        }
    }
    "ask" {
        Assert-EnvFile
        if ($RemainingArgs.Count -gt 0) {
            $question = $RemainingArgs -join " "
        }
        else {
            $question = Read-Host "MiniCode"
        }
        if ([string]::IsNullOrWhiteSpace($question)) {
            throw "Question must not be empty."
        }
        & $python $launcher ask $question --workspace $projectRoot --env-file $envFile
        $commandExitCode = $LASTEXITCODE
    }
}

if ($commandExitCode -ne 0) {
    Write-Error "MiniCode command failed with exit code $commandExitCode"
}
