<#
.SYNOPSIS
    Локальная проверка проекта Cooking на Windows перед отправкой на сервер.

.DESCRIPTION
    Повторяет логику tests/run_all.sh, но для Windows:
      1. активирует .venv (если есть);
      2. проверяет синтаксис всех production-модулей;
      3. запускает линтер, если он настроен;
      4. копирует проект в <диск>:\tmp\ct — тесты жёстко зашивают путь /tmp/ct,
         поэтому проверка идёт на копии и не трогает рабочие файлы и базы;
      5. запускает ВСЕ tests\test_*.py по очереди и останавливается на первом провале.

.PARAMETER Test
    Запустить только тесты, чьё имя содержит указанную строку (можно несколько).

.PARAMETER KeepGoing
    Не останавливаться на первом провале: прогнать все тесты и вернуть код 1.

.PARAMETER SkipUi
    Не запускать UI-тесты (test_ui*).

.PARAMETER SkipLoad
    Не запускать нагрузочный test_load.

.PARAMETER TimeoutSec
    Таймаут одного теста в секундах (по умолчанию 240).

.EXAMPLE
    .\check.ps1

.EXAMPLE
    .\check.ps1 -Test book,seobook

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\check.ps1
#>
[CmdletBinding()]
param(
    [string[]]$Test = @(),
    [switch]$KeepGoing,
    [switch]$SkipUi,
    [switch]$SkipLoad,
    [int]$TimeoutSec = 240
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }
Set-Location -LiteralPath $ProjectRoot

# Консоль и дочерние python-процессы — в UTF-8, иначе кириллица и эмодзи ломают вывод.
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch { }
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'

# Локально .env обычно нет, а bot.py проверяет формат токена при импорте.
# Тестам достаточно фиктивного токена: Telegram они не вызывают.
# Если .env есть, не перекрываем его значения (dotenv не перезаписывает env).
if (-not $env:BOT_TOKEN -and -not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) {
    $env:BOT_TOKEN = '123456789:TEST-TOKEN-LOCAL-NOT-REAL'
    Write-Host 'BOT_TOKEN не задан — для тестов подставлен фиктивный токен.' -ForegroundColor Yellow
}

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "== $Text" -ForegroundColor Cyan
}
function Write-Ok([string]$Text)   { Write-Host $Text -ForegroundColor Green }
function Write-Bad([string]$Text)  { Write-Host $Text -ForegroundColor Red }
function Write-Skip([string]$Text) { Write-Host $Text -ForegroundColor Yellow }

# ---------------------------------------------------------------- окружение

Write-Step 'Окружение'

$activate = Join-Path $ProjectRoot '.venv\Scripts\Activate.ps1'
if (Test-Path -LiteralPath $activate) {
    try {
        . $activate
        Write-Host "Активировано: $activate"
    } catch {
        Write-Skip "Не удалось активировать .venv: $($_.Exception.Message)"
    }
} else {
    Write-Skip 'Файл .venv\Scripts\Activate.ps1 не найден — активация пропущена.'
}

$python = $null
foreach ($rel in @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe')) {
    $candidate = Join-Path $ProjectRoot $rel
    if (Test-Path -LiteralPath $candidate) { $python = $candidate; break }
}
if (-not $python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) {
    Write-Bad 'Python не найден. Установите Python 3.12 и зависимости из requirements.txt.'
    exit 2
}
Write-Host "Python: $python"
& $python -c "import sys; print('Версия:', sys.version.split()[0])"

# ---------------------------------------------------------------- 1. синтаксис

Write-Step '1/3. Синтаксис production-модулей'

$pyFiles = @(Get-ChildItem -LiteralPath $ProjectRoot -Filter '*.py' -File |
    ForEach-Object { $_.FullName })
$storesDir = Join-Path $ProjectRoot 'stores'
if (Test-Path -LiteralPath $storesDir) {
    $pyFiles += @(Get-ChildItem -LiteralPath $storesDir -Filter '*.py' -File |
        ForEach-Object { $_.FullName })
}
if ($pyFiles.Count -eq 0) {
    Write-Bad 'В корне проекта не найдено ни одного .py-файла.'
    exit 2
}

$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $python -m py_compile @pyFiles
    $compileCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEap
}
if ($compileCode -ne 0) {
    Write-Bad 'Синтаксическая ошибка в production-модулях — исправьте её перед тестами.'
    exit 2
}
Write-Ok ("OK: {0} модулей компилируются без ошибок." -f $pyFiles.Count)

# ---------------------------------------------------------------- 2. линтер

Write-Step '2/3. Линтер'

$configNames = @('pyproject.toml', 'setup.cfg', '.flake8', 'ruff.toml', '.pylintrc', 'tox.ini')
$configs = @($configNames | ForEach-Object { Join-Path $ProjectRoot $_ } |
    Where-Object { Test-Path -LiteralPath $_ })

$lintTool = $null
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    foreach ($tool in @('ruff', 'flake8', 'pylint')) {
        if (Get-Command $tool -ErrorAction SilentlyContinue) { $lintTool = $tool; break }
        & $python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$tool') else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $lintTool = $tool; break }
    }
} finally {
    $ErrorActionPreference = $prevEap
}

if ($lintTool) {
    Write-Host "Найден $lintTool — запускаю проверку."
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        switch ($lintTool) {
            'ruff'   { & $python -m ruff check $ProjectRoot --exclude .venv,venv,__pycache__ }
            'flake8' { & $python -m flake8 $ProjectRoot --exclude=.venv,venv,__pycache__ }
            'pylint' { & $python -m pylint $ProjectRoot --ignore=.venv,venv,__pycache__ --disable=all --enable=E }
        }
        $lintCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if ($lintCode -ne 0) {
        Write-Bad "Линтер ($lintTool) нашёл ошибки — исправьте их перед тестами."
        exit 2
    }
    Write-Ok "OK: $lintTool без ошибок."
} elseif ($configs.Count -gt 0) {
    $names = ($configs | ForEach-Object { Split-Path -Leaf $_ }) -join ', '
    Write-Skip "Конфиг линтера есть ($names), но сам линтер не установлен — пропуск."
} else {
    Write-Skip 'Линтер в проекте не настроен (нет ruff/flake8/pylint и конфигов) — пропуск.'
}

# ---------------------------------------------------------------- 3. копия

Write-Step '3/3. Копия проекта для тестов'

$drive = (Get-Item -LiteralPath $ProjectRoot).PSDrive.Name
if (-not $drive -or $drive.Length -ne 1) {
    Write-Bad "Не удалось определить диск проекта ('$drive'). Тесты ждут путь <диск>:\tmp\ct."
    exit 2
}
$staging = "${drive}:\tmp\ct"

if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Path $staging -Force | Out-Null

$excludeDirs = @('.git', 'venv', '.venv', '__pycache__', 'node_modules', 'Claude outputs')
$roboArgs = @($ProjectRoot, $staging, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XF', '*.pyc')
foreach ($dir in $excludeDirs) {
    $roboArgs += @('/XD', (Join-Path $ProjectRoot $dir))
}
& robocopy @roboArgs | Out-Null
$roboCode = $LASTEXITCODE
if ($roboCode -ge 8) {
    Write-Bad "Не удалось скопировать проект в $staging (robocopy, код $roboCode)."
    exit 2
}
Write-Ok "OK: проект скопирован в $staging"

# ---------------------------------------------------------------- тесты

$testsDir = Join-Path $ProjectRoot 'tests'
$allTests = @(Get-ChildItem -LiteralPath $testsDir -Filter 'test_*.py' -File |
    Sort-Object Name | ForEach-Object { $_.BaseName })
if ($allTests.Count -eq 0) {
    Write-Bad "В $testsDir нет файлов test_*.py."
    exit 2
}

# Порядок как в tests/run_all.sh, затем остальные (в т.ч. 12 не подключённых к раннеру).
$preferred = @(
    'test_lists', 'test_social', 'test_social2', 'test_social_images', 'test_v2_bot',
    'test_missing', 'test_reminder', 'test_bot_timers', 'test_bot_book', 'test_diary',
    'test_payanyway', 'test_book', 'test_v2_web', 'test_v2_admin', 'test_help',
    'test_privacy', 'test_vk_login', 'test_web_missing', 'test_abuse', 'test_limits',
    'test_promo', 'test_launch', 'test_cards_pages', 'test_vision', 'test_double',
    'test_bot_pdf', 'test_meal_order', 'test_extras', 'test_friends', 'test_stars',
    'test_audit', 'test_qty', 'test_foodsearch_js', 'test_buttons', 'test_share',
    'test_gen', 'test_speed', 'test_job_js', 'test_digest', 'test_combo', 'test_tailor',
    'test_audit2', 'test_mylist', 'test_shelfcook'
)
$ordered = New-Object System.Collections.Generic.List[string]
foreach ($name in $preferred) {
    if ($allTests -contains $name) { $ordered.Add($name) }
}
foreach ($name in $allTests) {
    if (-not $ordered.Contains($name)) { $ordered.Add($name) }
}

# -File передаёт "a,b" одной строкой, поэтому фильтр дополнительно бьём по запятым.
$patterns = @()
foreach ($item in $Test) {
    foreach ($part in ($item -split ',')) {
        if ($part.Trim()) { $patterns += $part.Trim() }
    }
}
if ($patterns.Count -gt 0) {
    $selected = New-Object System.Collections.Generic.List[string]
    foreach ($name in $ordered) {
        foreach ($pattern in $patterns) {
            if ($name -like "*$pattern*") { $selected.Add($name); break }
        }
    }
    $ordered = $selected
}
if ($ordered.Count -eq 0) {
    Write-Bad "По фильтру -Test '$($patterns -join ', ')' не найдено ни одного теста."
    exit 2
}

$script:cdpPresent = Test-Path -LiteralPath (Join-Path $testsDir 'cdp.py')
$script:sitePortOpen = $null

function Test-PythonModule([string]$Name) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$Name') else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Test-SitePort {
    if ($null -ne $script:sitePortOpen) { return $script:sitePortOpen }
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', 8123, $null, $null)
        $open = $iar.AsyncWaitHandle.WaitOne(300)
        if ($open) { $client.EndConnect($iar) }
        $script:sitePortOpen = [bool]$open
    } catch {
        $script:sitePortOpen = $false
    } finally {
        $client.Close()
    }
    return $script:sitePortOpen
}

function Get-SkipReason([string]$Name) {
    if ($Name -like 'test_ui*') {
        if ($SkipUi) { return 'UI-тесты отключены ключом -SkipUi' }
        if (-not $script:cdpPresent) { return 'в репозитории нет tests\cdp.py — UI-тесты неполные' }
        if ($Name -eq 'test_ui2' -and -not (Test-PythonModule 'fitz')) { return 'нет модуля pymupdf (fitz)' }
        if (-not (Test-SitePort)) { return 'сайт не запущен на 127.0.0.1:8123' }
    }
    if ($Name -eq 'test_load' -and $SkipLoad) { return 'нагрузочный тест отключён ключом -SkipLoad' }
    return $null
}

function Invoke-PythonTest {
    param(
        [string]$ScriptPath,
        [string[]]$ScriptArgs,
        [int]$Timeout
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $python
    $argList = @()
    if ($null -ne $ScriptArgs) { $argList = @($ScriptArgs) }
    $quoted = @($ScriptPath) + $argList | ForEach-Object { '"' + $_ + '"' }
    $psi.Arguments = ($quoted -join ' ')
    $psi.WorkingDirectory = $staging
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $psi.EnvironmentVariables['PYTHONUTF8'] = '1'
    $psi.EnvironmentVariables['PYTHONUNBUFFERED'] = '1'

    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $proc = [System.Diagnostics.Process]::Start($psi)
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    $finished = $proc.WaitForExit($Timeout * 1000)
    if (-not $finished) {
        try { $proc.Kill() } catch { }
        $proc.WaitForExit(5000) | Out-Null
    } else {
        $proc.WaitForExit()
    }
    $watch.Stop()

    $stdout = ''
    $stderr = ''
    try { $stdout = $outTask.Result } catch { }
    try { $stderr = $errTask.Result } catch { }

    return [pscustomobject]@{
        TimedOut  = (-not $finished)
        ExitCode  = $(if ($finished) { $proc.ExitCode } else { -1 })
        Output    = ($stdout + "`n" + $stderr)
        Seconds   = $watch.Elapsed.TotalSeconds
    }
}

function Get-FailureLines([string]$Output) {
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($Output -split "`r?`n")) {
        if ($line -match '^(FAIL |ИТОГ: ПРОВАЛЕНО)') { $found.Add($line); continue }
        if ($line -match '^Traceback \(most recent call last\)') { $found.Add($line); continue }
        if ($line -match '^[A-Za-z_.]*(Error|Exception):') { $found.Add($line); continue }
    }
    return $found
}

function Show-Failure {
    param($Label, $Result, $LogPath, [int]$Timeout, [int]$Index, [int]$Total)
    Write-Bad ("[FAIL] [{0}/{1}] {2}  (код {3}, {4:N1} c)" -f $Index, $Total, $Label, $Result.ExitCode, $Result.Seconds)
    if ($Result.TimedOut) { Write-Bad ("       таймаут {0} c — тест завис" -f $Timeout) }

    $lines = @(Get-FailureLines $Result.Output)
    if ($lines.Count -gt 0) {
        $shown = 0
        foreach ($line in $lines) {
            if ($shown -ge 20) { Write-Bad '       ...'; break }
            Write-Bad ('       ' + $line.TrimEnd())
            $shown++
        }
    } else {
        $tail = @($Result.Output -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 10)
        if ($tail.Count -eq 0) {
            Write-Bad '       тест ничего не вывел — проверьте, что он вообще запустился'
        } else {
            foreach ($line in $tail) { Write-Bad ('       ' + $line.TrimEnd()) }
        }
    }
    Write-Host ("       полный лог: {0}" -f $LogPath) -ForegroundColor DarkGray
}

$logDir = Join-Path $env:TEMP 'cooking_check_logs'
if (Test-Path -LiteralPath $logDir) {
    Remove-Item -Path (Join-Path $logDir '*.log') -Force -ErrorAction SilentlyContinue
} else {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

Write-Step ("Тесты: {0} файлов" -f $ordered.Count)

$total = $ordered.Count
$passed = 0
$skipped = 0
$failed = 0
$index = 0
$failedNames = New-Object System.Collections.Generic.List[string]
$started = Get-Date

foreach ($name in $ordered) {
    $index++
    $label = $name
    $reason = Get-SkipReason $name
    if ($reason) {
        Write-Skip ("[{0}/{1}] {2} — SKIP: {3}" -f $index, $total, $label, $reason)
        $skipped++
        continue
    }

    $testPath = Join-Path $testsDir ($name + '.py')
    # Список, а не массив: @(@()) в PowerShell разворачивается в пустой массив.
    $argSets = New-Object System.Collections.Generic.List[object]
    if ($name -eq 'test_load') {
        # test_load.py требует режим: bot или web.
        $argSets.Add(@('bot'))
        $argSets.Add(@('web'))
    } else {
        $argSets.Add(@())
    }

    $combined = ''
    $seconds = 0.0
    $exitCode = 0
    $timedOut = $false
    $runOk = $true
    $loadRuns = 0
    $loadOkRuns = 0

    foreach ($argSet in $argSets) {
        $res = Invoke-PythonTest -ScriptPath $testPath -ScriptArgs $argSet -Timeout $TimeoutSec
        $combined += $res.Output + "`n"
        $seconds += $res.Seconds
        if ($res.TimedOut) { $timedOut = $true; $runOk = $false }
        if ($res.ExitCode -ne 0) { $exitCode = $res.ExitCode; $runOk = $false }
        if ($name -eq 'test_load') {
            $loadRuns++
            if ($res.Output -match '(?m)^ИТОГ:\s*ВСЁ ОК') { $loadOkRuns++ }
        }
    }

    $failLines = @(Get-FailureLines $combined)
    if ($failLines.Count -gt 0) { $runOk = $false }

    $passCount = ([regex]::Matches($combined, '(?m)^PASS ')).Count
    $loadOk = ($name -eq 'test_load') -and ($loadRuns -gt 0) -and ($loadOkRuns -eq $loadRuns)

    if ($name -eq 'test_load') {
        if (-not $loadOk) { $runOk = $false }
    } elseif ($passCount -eq 0) {
        $runOk = $false
    }

    $logPath = Join-Path $logDir (($label -replace '[^\w\.-]', '_') + '.log')
    [System.IO.File]::WriteAllText($logPath, $combined, (New-Object System.Text.UTF8Encoding($false)))

    $result = [pscustomobject]@{
        TimedOut = $timedOut
        ExitCode = $exitCode
        Output   = $combined
        Seconds  = $seconds
    }

    if ($runOk) {
        if ($name -eq 'test_load') {
            Write-Ok ("[{0}/{1}] {2} — OK (нагрузка: bot + web, {3:N1} c)" -f $index, $total, $label, $seconds)
        } else {
            Write-Ok ("[{0}/{1}] {2} — OK ({3} проверок, {4:N1} c)" -f $index, $total, $label, $passCount, $seconds)
        }
        $passed++
    } else {
        $failed++
        $failedNames.Add($label)
        Show-Failure -Label $label -Result $result -LogPath $logPath -Timeout $TimeoutSec -Index $index -Total $total
        if (-not $KeepGoing) {
            Write-Host ''
            Write-Bad ("Остановлено на первом провале: {0}" -f $label)
            Write-Host ("Полные логи: {0}" -f $logDir)
            exit 1
        }
    }
}

# ---------------------------------------------------------------- итог

$elapsed = (Get-Date) - $started
Write-Step 'Итог'
Write-Host ("Тестов: {0}   OK: {1}   SKIP: {2}   FAIL: {3}" -f $total, $passed, $skipped, $failed)
Write-Host ("Время: {0:mm\:ss}" -f $elapsed)
Write-Host ("Логи:  {0}" -f $logDir)
Write-Host ("Копия: {0}" -f $staging)
if ($failedNames.Count -gt 0) {
    Write-Bad ("Провалились: {0}" -f ($failedNames -join ', '))
}

if ($failed -gt 0) {
    Write-Bad 'Есть провалы — на сервер отправлять нельзя.'
    exit 1
}
Write-Ok 'Все проверки пройдены — можно отправлять на сервер.'
exit 0
