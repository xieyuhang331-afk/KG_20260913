. (Join-Path $PSScriptRoot '本机联调公共.ps1')
try{
  $s=Read-KgState
  Assert-KgOwnedResources $s
  Stop-KgOwnedProcesses $s.processes
  $s.processes=@{}
  $env:KG_LOCAL_RUN_ID=$s.run_id;$env:KG_LOCAL_DATABASE_CONTAINER=$s.database_container;$env:KG_LOCAL_RABBITMQ_CONTAINER=$s.rabbitmq_container;$env:KG_LOCAL_CLAMD_CONTAINER=$s.clamav_container;$env:KG_LOCAL_DATABASE_VOLUME=$s.database_volume;$env:KG_LOCAL_RABBITMQ_VOLUME=$s.rabbitmq_volume;$env:KG_LOCAL_NETWORK=$s.network_name;$env:KG_LOCAL_CLAMD_SIGNATURE_VOLUME=$s.clamav_signature_volume;$env:KG_LOCAL_CLAMD_CONFIG_VOLUME=$s.clamav_config_volume;$env:KG_LOCAL_DATABASE_PORT=$s.database_port;$env:KG_LOCAL_RABBITMQ_PORT=$s.rabbitmq_port;$env:KG_LOCAL_CLAMD_PORT=$s.clamav_port
  $env:KG_LOCAL_POSTGRES_PASSWORD=$s.runtime.KG_LOCAL_POSTGRES_PASSWORD;$env:KG_LOCAL_RABBITMQ_USER=$s.runtime.KG_LOCAL_RABBITMQ_USER;$env:KG_LOCAL_RABBITMQ_PASSWORD=$s.runtime.KG_LOCAL_RABBITMQ_PASSWORD
  $args=Get-KgComposeArgs $s
  & docker @args stop|Out-Null
  if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_STOP_FAILED'}
  Write-KgState $s
  @{status='STOPPED';code='STOPPED';data_preserved=$true}|ConvertTo-Json -Compress
}catch{@{status='FAILED';code=if($_.Exception.Message-match'^KG_'){$_.Exception.Message}else{'KG_LOCAL_STOP_FAILED'}}|ConvertTo-Json -Compress;exit 1}
