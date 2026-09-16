. (Join-Path $PSScriptRoot '本机联调公共.ps1')

$state = $null
$mutex = $null
$lockTaken = $false
try {
  if ($args.Count -ne 0) { Fail-Kg 'KG_G2_BOOTSTRAP_ARGUMENT_INVALID' }
  $state = Read-KgState
  Assert-KgOwnedResources $state
  if ([string]$state.migration_head -ne '20260915_0048') { Fail-Kg 'KG_G2_BOOTSTRAP_SCOPE_INVALID' }
  $run = [string]$state.run_id
  $mutex = [System.Threading.Mutex]::new($false, "Local\KG_G2_B_$run")
  $lockTaken = $mutex.WaitOne([TimeSpan]::FromSeconds(30))
  if (-not $lockTaken) { Fail-Kg 'KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN' }

  $stateDir = Assert-KgInside ([string]$state.state_dir) $script:状态根
  $prepared = Join-Path $stateDir 'G2控制面凭据.json'
  $preparedTemp = Join-Path $stateDir 'G2控制面凭据.prepared.tmp'
  $receipt = Join-Path $stateDir 'G2控制面初始化回执.json'
  $receiptTemp = Join-Path $stateDir 'G2控制面初始化回执.tmp'
  if (Test-Path -LiteralPath $prepared) {
    Assert-KgNoReparsePath $prepared
    Assert-KgPrivateAcl $prepared
    if (Test-Path -LiteralPath $preparedTemp) { Fail-Kg 'KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN' }
  } else {
    if (Test-Path -LiteralPath $preparedTemp) { Fail-Kg 'KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN' }
    Initialize-KgPrivateFile $preparedTemp
  }
  if (Test-Path -LiteralPath $receipt) {
    Assert-KgNoReparsePath $receipt
    Assert-KgPrivateAcl $receipt
    if (Test-Path -LiteralPath $receiptTemp) { Fail-Kg 'KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN' }
  } else {
    if (Test-Path -LiteralPath $receiptTemp) {
      Assert-KgNoReparsePath $receiptTemp
      Assert-KgPrivateAcl $receiptTemp
    } else {
      Initialize-KgPrivateFile $receiptTemp
    }
  }

  $storageSentinel = Join-Path $stateDir '私有文件/.g1-persistence-sentinel'
  if (-not (Test-Path -LiteralPath $storageSentinel -PathType Leaf) -or
      [string](Get-Content -LiteralPath $storageSentinel -Raw -Encoding ascii) -ne [string]$state.sentinel) {
    Fail-Kg 'KG_G2_BOOTSTRAP_SCOPE_INVALID'
  }

  $postgresPassword = [uri]::EscapeDataString([string]$state.runtime.KG_LOCAL_POSTGRES_PASSWORD)
  $databaseName = [string]$state.runtime.KG_DATABASE_NAME
  $databasePort = [string]$state.database_port
  if ($databaseName -ne "kg_it_$run" -or $databasePort -notmatch '^[0-9]{2,5}$') { Fail-Kg 'KG_G2_BOOTSTRAP_SCOPE_INVALID' }
  $ownerUrl = "postgresql+asyncpg://postgres:$postgresPassword@127.0.0.1:$databasePort/$databaseName"
  $python = Join-Path $script:仓库根 'backend/.venv/Scripts/python.exe'
  $runner = Join-Path $script:仓库根 'backend/scripts/初始化G2首个平台审核账号.py'
  if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $runner -PathType Leaf)) { Fail-Kg 'KG_G2_BOOTSTRAP_SCOPE_INVALID' }

  $process = [Diagnostics.Process]::new()
  $process.StartInfo = [Diagnostics.ProcessStartInfo]::new()
  $process.StartInfo.FileName = $python
  $process.StartInfo.ArgumentList.Add($runner)
  $process.StartInfo.WorkingDirectory = Join-Path $script:仓库根 'backend'
  $process.StartInfo.UseShellExecute = $false
  $process.StartInfo.RedirectStandardOutput = $true
  $process.StartInfo.RedirectStandardError = $true
  foreach ($entry in $state.runtime.PSObject.Properties) {
    if ($null -ne $entry.Value) { $process.StartInfo.Environment[$entry.Name] = [string]$entry.Value }
  }
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_OWNER_DATABASE_URL'] = $ownerUrl
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_ENVIRONMENT'] = 'local_ephemeral'
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_DATABASE_HOST'] = '127.0.0.1'
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_DATABASE_NAME'] = $databaseName
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_RUN_ID'] = $run
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_SENTINEL'] = [string]$state.sentinel
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_MIGRATION_HEAD'] = [string]$state.migration_head
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_PREPARED_PATH'] = $prepared
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_PREPARED_TEMP_PATH'] = $preparedTemp
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_RECEIPT_PATH'] = $receipt
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_RECEIPT_TEMP_PATH'] = $receiptTemp
  $process.StartInfo.Environment['KG_G2_BOOTSTRAP_API_BASE_URL'] = "http://127.0.0.1:$($state.api_port)"
  $process.StartInfo.Environment['PYTHONPATH'] = Join-Path $script:仓库根 'backend'
  if (-not $process.Start()) { Fail-Kg 'KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN' }
  $safeOutput = $process.StandardOutput.ReadToEnd()
  $process.StandardError.ReadToEnd() | Out-Null
  $process.WaitForExit()
  if ($process.ExitCode -ne 0) {
    if ($safeOutput -match '"code":"(KG_G2_BOOTSTRAP_[A-Z_]+)"') { Fail-Kg $Matches[1] }
    Fail-Kg 'KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN'
  }
  Assert-KgPrivateAcl $prepared
  Assert-KgPrivateAcl $receipt
  $safeOutput.Trim()
} catch {
  $failureCode = if ($_.Exception.Message -match '^KG_') { $_.Exception.Message } else { 'KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN' }
  @{status='FAILED';code=$failureCode} | ConvertTo-Json -Compress
  exit 1
} finally {
  [Environment]::SetEnvironmentVariable('KG_G2_BOOTSTRAP_OWNER_DATABASE_URL',$null,'Process')
  if ($lockTaken -and $null -ne $mutex) { $mutex.ReleaseMutex() }
  if ($null -ne $mutex) { $mutex.Dispose() }
  $ownerUrl = $null
}
