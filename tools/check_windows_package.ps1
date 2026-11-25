# Run with PowerShell 7 on Windows after python packaging/build.py.
[CmdletBinding()]
param([string]$Archive = (Join-Path $PSScriptRoot '../release/AdaptiveSubs-Windows.zip'))
$ErrorActionPreference = 'Stop'
$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$work = Join-Path ([IO.Path]::GetTempPath()) ('asubs-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$extract = Join-Path $work '解压目录'
$userData = Join-Path $work '用户数据'
$process = $null
try {
    [IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $extract)
    $package = Join-Path $extract 'AdaptiveSubs-Windows'
    $extension = Join-Path $package '浏览器插件'
    $manifest = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $extension 'manifest.json') | ConvertFrom-Json
    foreach ($entry in $manifest.content_scripts) {
        foreach ($file in (@($entry.js) + @($entry.css))) {
            if (!(Test-Path -PathType Leaf -LiteralPath (Join-Path $extension $file))) {
                throw "Missing extension resource: $file"
            }
        }
    }

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { $port = $listener.LocalEndpoint.Port } finally { $listener.Stop() }
    $base = "http://127.0.0.1:$port"
    $headers = @{ 'X-Adaptive-Subs' = '1' }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = Join-Path $package '程序/AdaptiveSubs.exe'
    $start.WorkingDirectory = $work
    $start.UseShellExecute = $false
    $start.ArgumentList.Add('--port')
    $start.ArgumentList.Add([string]$port)
    foreach ($key in @('OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'MIMO_API_KEY', 'ASUBS_HEADLESS')) {
        [void]$start.Environment.Remove($key)
    }
    $start.Environment['ASUBS_HOME'] = $userData
    $process = [Diagnostics.Process]::Start($start)
    $deadline = [DateTime]::UtcNow.AddMinutes(2)
    $status = $null
    do {
        $process.Refresh()
        if ($process.HasExited) { throw "Program exited during startup: $($process.ExitCode)" }
        if ($process.MainWindowTitle -eq 'Adaptive Subs') {
            try { $status = Invoke-RestMethod "$base/api/status" -Headers $headers -NoProxy -TimeoutSec 3 }
            catch { $status = $null }
        }
        if ($status) { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    if (!$status -or $status.home -ne $userData -or $status.extension_dir -ne $extension) {
        throw 'Could not confirm the native window, isolated server, and packaged extension directory.'
    }
    $health = Invoke-RestMethod "$base/api/health" -NoProxy -TimeoutSec 5
    $profile = Invoke-RestMethod "$base/api/profile" -Headers $headers -NoProxy -TimeoutSec 10
    if (!$health.ok -or $health.key -or !$profile.known.Count) { throw 'Invalid initial health or vocabulary profile.' }
    foreach ($page in @('/app', '/setup', '/profile', '/player.html', '/assets/app.js', '/data/vocab_test.json')) {
        $response = Invoke-WebRequest "$base$page" -NoProxy -TimeoutSec 5
        if ($response.StatusCode -ne 200 -or $response.RawContentLength -le 0) { throw "Empty packaged route: $page" }
    }
    $quit = Invoke-RestMethod "$base/api/quit" -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -NoProxy -TimeoutSec 5
    if (!$quit.ok -or !$process.WaitForExit(15000) -or $process.ExitCode -ne 0) { throw 'Native shutdown failed.' }
    Write-Host 'Verified the extracted Windows executable, native window, control pages, bundled vocabulary, extension paths and clean exit.'
} catch {
    $log = Join-Path $userData 'logs/adaptive-subs.log'
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Encoding utf8 -Tail 30 }
    throw
} finally {
    if ($process) {
        if (!$process.HasExited) { $process.Kill($true); $process.WaitForExit() }
        $process.Dispose()
    }
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}
