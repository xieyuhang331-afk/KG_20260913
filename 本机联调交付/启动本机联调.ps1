. (Join-Path $PSScriptRoot '本机联调公共.ps1')
$readinessAttempts=3
$readinessHistory=@()
$s=$null
$startedThisAttempt=$false
try {
  $s=Read-KgState; if((Get-KgSource).head-ne$s.source.head){Fail-Kg 'KG_LOCAL_SOURCE_HEAD_CHANGED'}
  foreach($p in $s.processes.psobject.Properties){if(Test-KgProcessIdentity $p.Value){Fail-Kg 'KG_LOCAL_ALREADY_RUNNING'}elseif(Get-Process -Id ([int]$p.Value.pid) -ErrorAction SilentlyContinue){Fail-Kg 'KG_LOCAL_PROCESS_OWNERSHIP_INVALID'}}
  Assert-KgOwnedResources $s
  $env:KG_LOCAL_RUN_ID=$s.run_id;$env:KG_LOCAL_DATABASE_CONTAINER=$s.database_container;$env:KG_LOCAL_RABBITMQ_CONTAINER=$s.rabbitmq_container;$env:KG_LOCAL_CLAMD_CONTAINER=$s.clamav_container;$env:KG_LOCAL_DATABASE_VOLUME=$s.database_volume;$env:KG_LOCAL_RABBITMQ_VOLUME=$s.rabbitmq_volume;$env:KG_LOCAL_NETWORK=$s.network_name;$env:KG_LOCAL_CLAMD_SIGNATURE_VOLUME=$s.clamav_signature_volume;$env:KG_LOCAL_CLAMD_CONFIG_VOLUME=$s.clamav_config_volume;$env:KG_LOCAL_DATABASE_PORT=$s.database_port;$env:KG_LOCAL_RABBITMQ_PORT=$s.rabbitmq_port;$env:KG_LOCAL_CLAMD_PORT=$s.clamav_port
  $env:KG_LOCAL_POSTGRES_PASSWORD=$s.runtime.KG_LOCAL_POSTGRES_PASSWORD;$env:KG_LOCAL_RABBITMQ_USER=$s.runtime.KG_LOCAL_RABBITMQ_USER;$env:KG_LOCAL_RABBITMQ_PASSWORD=$s.runtime.KG_LOCAL_RABBITMQ_PASSWORD
  foreach($entry in $s.runtime.psobject.Properties){[Environment]::SetEnvironmentVariable($entry.Name,[string]$entry.Value,'Process')}
  Assert-KgClamdConfigVolume $s
  $args=Get-KgComposeArgs $s; & docker @args start|Out-Null; Wait-KgHealthy $s.database_container; Wait-KgHealthy $s.rabbitmq_container; Wait-KgHealthy $s.clamav_container 180
  $python=Join-Path $script:仓库根 'backend/.venv/Scripts/python.exe'; if(-not(Test-Path $python)){Fail-Kg 'KG_LOCAL_PREREQUISITE_FAILED'}
  $env:KG_ENV='local';$env:KG_DEPLOYMENT_PROFILE='local_ephemeral';$env:KG_FILE_STORAGE_BACKEND='local_filesystem';$env:KG_PRIVATE_FILE_STORAGE_BACKEND='local_filesystem';$env:KG_PRIVATE_FILE_STORAGE_ROOT=Join-Path $s.state_dir '私有文件'
  $env:KG_PRIVATE_FILE_SCANNER_FACTORY=$s.runtime.KG_PRIVATE_FILE_SCANNER_FACTORY;$env:KG_PRIVATE_FILE_CLAMD_HOST=$s.runtime.KG_PRIVATE_FILE_CLAMD_HOST;$env:KG_PRIVATE_FILE_CLAMD_PORT=$s.runtime.KG_PRIVATE_FILE_CLAMD_PORT;$env:KG_PRIVATE_FILE_SCANNER_ENGINE_VERSION=$s.runtime.KG_PRIVATE_FILE_SCANNER_ENGINE_VERSION;$env:KG_PRIVATE_FILE_SCANNER_TIMEOUT_SECONDS=$s.runtime.KG_PRIVATE_FILE_SCANNER_TIMEOUT_SECONDS;$env:KG_PRIVATE_FILE_SCANNER_MAX_SIGNATURE_AGE_HOURS=$s.runtime.KG_PRIVATE_FILE_SCANNER_MAX_SIGNATURE_AGE_HOURS
  $env:KG_CELERY_BROKER_URL="amqp://$($s.runtime.KG_LOCAL_RABBITMQ_USER):$($s.runtime.KG_LOCAL_RABBITMQ_PASSWORD)@127.0.0.1:$($s.rabbitmq_port)//"
  $queues=@{registration='registration';private_file='private-file';therapist='therapist-workflow';member='member-enrollment-workflow';slice4='slice4-health-workflow';slice5='slice5-assessment-workflow';slice6='slice6-health-plan-workflow';slice7='slice7-service-fulfillment-workflow'};$hosts=[ordered]@{}
  $startedThisAttempt=$true
  foreach($k in $queues.Keys){$workerHostname="$k-$($s.run_id)@localhost";$hosts[$k]=$workerHostname;Start-KgHidden $s "worker-$k" (Join-Path $script:仓库根 'backend') "& '$python' -m celery -A app.tasks.celery_app:celery_app worker --loglevel=WARNING --pool=solo --concurrency=1 --queues=$($queues[$k]) --hostname=$workerHostname"};$s.worker_hosts=$hosts
  Start-KgHidden $s 'beat' (Join-Path $script:仓库根 'backend') "& '$python' -m celery -A app.tasks.celery_app:celery_app beat --loglevel=WARNING --schedule '$($s.state_dir)\celerybeat.dat'"
  Start-KgHidden $s 'api' (Join-Path $script:仓库根 'backend') "& '$python' -m uvicorn app.main:app --host 127.0.0.1 --port $($s.api_port)"
  Start-KgHidden $s 'platform-web' (Join-Path $script:仓库根 'frontend') "npm run dev -- --host 127.0.0.1 --port $($s.platform_port)"
  Start-KgHidden $s 'institution-web' (Join-Path $script:仓库根 'frontend') "npm run dev -- --host 127.0.0.1 --port $($s.institution_port)"
  Write-KgState $s
  for($attempt=1;$attempt-le$readinessAttempts;$attempt++){
    Start-Sleep -Seconds 5
    $readinessOutput=& (Join-Path $PSScriptRoot '检查本机联调状态.ps1');$readinessExit=$LASTEXITCODE
    $readinessResult=$readinessOutput|Select-Object -Last 1|ConvertFrom-Json
    $readinessHistory+=,[ordered]@{attempt=$attempt;code=$readinessResult.code}
    if($readinessExit-eq0-or$readinessResult.code-eq'KG_LOCAL_CAPABILITY_BLOCKED'){
      $readinessResult|Add-Member -NotePropertyName readiness_attempt -NotePropertyValue $attempt -Force
      $readinessResult|Add-Member -NotePropertyName readiness_attempt_limit -NotePropertyValue $readinessAttempts -Force
      $readinessResult|Add-Member -NotePropertyName readiness_history -NotePropertyValue $readinessHistory -Force
      $readinessResult|ConvertTo-Json -Depth 6 -Compress
      if($readinessExit-eq0){exit 0}else{exit 1}
    }
    if($readinessResult.code-ne'KG_LOCAL_READINESS_INCOMPLETE'){Fail-Kg $readinessResult.code}
  }
  Fail-Kg 'KG_LOCAL_READINESS_INCOMPLETE'
} catch {
  $failureCode=if($_.Exception.Message-match'^KG_'){$_.Exception.Message}else{'KG_LOCAL_START_FAILED'}
  if($startedThisAttempt-and$s-and$s.processes){
    try{Stop-KgOwnedProcesses $s.processes;$s.processes=@{};Write-KgState $s}catch{$failureCode=if($_.Exception.Message-match'^KG_'){$_.Exception.Message}else{'KG_LOCAL_PROCESS_STOP_FAILED'}}
  }
  @{status='NOT_READY';code=$failureCode;readiness_attempt=$readinessHistory.Count;readiness_attempt_limit=$readinessAttempts;readiness_history=$readinessHistory}|ConvertTo-Json -Depth 6 -Compress
  exit 1
}
