. (Join-Path $PSScriptRoot '本机联调公共.ps1')
$script:KG_LOCAL_WORKER_PROBE_PROCESS_TIMEOUT_MS=4500
$script:KG_LOCAL_SCANNER_PROBE_PROCESS_TIMEOUT_MS=4500
function Test-KgWorkerProbe([string]$Python,[string]$Kind,[string]$Hostname){
  $env:KG_G1_PROBE_KIND=$Kind;$env:KG_G1_PROBE_HOSTNAME=$Hostname
  $code="import os,sys; from scripts.check_worker_readiness import probe_worker; expected={'status':'READY','worker_kind':os.environ['KG_G1_PROBE_KIND']}; sys.exit(0 if probe_worker(worker_kind=os.environ['KG_G1_PROBE_KIND'],hostname=os.environ['KG_G1_PROBE_HOSTNAME']) == expected else 1)"
  $info=[System.Diagnostics.ProcessStartInfo]::new();$info.FileName=$Python;$info.WorkingDirectory=(Join-Path $script:仓库根 'backend');$info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
  [void]$info.ArgumentList.Add('-c');[void]$info.ArgumentList.Add($code)
  $process=[System.Diagnostics.Process]::new();$process.StartInfo=$info
  try{
    if(-not$process.Start()){return $false}
    if(-not$process.WaitForExit($script:KG_LOCAL_WORKER_PROBE_PROCESS_TIMEOUT_MS)){$process.Kill($true);$process.WaitForExit();return $false}
    return $process.ExitCode-eq0
  }catch{return $false}finally{$process.Dispose()}
}
function Test-KgScannerProbe([string]$Python){
  $code="import asyncio,sys; from app.modules.private_file.clamav_scanner import build_clamav_scanner; scanner=build_clamav_scanner(); sys.exit(0 if asyncio.run(scanner.health()) else 1)"
  $info=[System.Diagnostics.ProcessStartInfo]::new();$info.FileName=$Python;$info.WorkingDirectory=(Join-Path $script:仓库根 'backend');$info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
  [void]$info.ArgumentList.Add('-c');[void]$info.ArgumentList.Add($code)
  $process=[System.Diagnostics.Process]::new();$process.StartInfo=$info
  try{
    if(-not$process.Start()){return $false}
    if(-not$process.WaitForExit($script:KG_LOCAL_SCANNER_PROBE_PROCESS_TIMEOUT_MS)){$process.Kill($true);$process.WaitForExit();return $false}
    return $process.ExitCode-eq0
  }catch{return $false}finally{$process.Dispose()}
}
try {
  $s=Read-KgState;$checks=[ordered]@{}
  Assert-KgOwnedResources $s
  foreach($entry in $s.runtime.psobject.Properties){[Environment]::SetEnvironmentVariable($entry.Name,[string]$entry.Value,'Process')}
  $env:KG_ENV='local';$env:KG_DEPLOYMENT_PROFILE='local_ephemeral';$env:KG_FILE_STORAGE_BACKEND='local_filesystem';$env:KG_PRIVATE_FILE_STORAGE_BACKEND='local_filesystem';$env:KG_PRIVATE_FILE_STORAGE_ROOT=Join-Path $s.state_dir '私有文件'
  Assert-KgClamdConfigVolume $s
  foreach($containerCheck in @(@('database_container',$s.database_container),@('rabbitmq_container',$s.rabbitmq_container),@('clamav_container',$s.clamav_container))){$v=docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $containerCheck[1] 2>$null;$checks[$containerCheck[0]]=($LASTEXITCODE-eq0-and$v-eq'healthy')}
  foreach($p in $s.processes.psobject.Properties){$checks[$p.Name]=Test-KgProcessIdentity $p.Value}
  $python=Join-Path $script:仓库根 'backend/.venv/Scripts/python.exe';$env:KG_CELERY_BROKER_URL="amqp://$($s.runtime.KG_LOCAL_RABBITMQ_USER):$($s.runtime.KG_LOCAL_RABBITMQ_PASSWORD)@127.0.0.1:$($s.rabbitmq_port)//"
  foreach($kind in @('registration','private_file','therapist','member','slice4','slice5','slice6','slice7')){$checks["worker_kind='$kind'"]=Test-KgWorkerProbe $python $kind $s.worker_hosts.$kind}
  $checks.scanner_health=Test-KgScannerProbe $python
  foreach($endpoint in @("http://127.0.0.1:$($s.api_port)/health/live","http://127.0.0.1:$($s.api_port)/health/ready")){try{$response=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri $endpoint;$checks[$endpoint]=($response.StatusCode-eq200)}catch{$checks[$endpoint]=$false}}
  $web_endpoints=@("http://127.0.0.1:$($s.platform_port)/","http://127.0.0.1:$($s.institution_port)/")
  foreach($endpoint in $web_endpoints){try{$response=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri $endpoint;$checks[$endpoint]=($response.StatusCode-eq200)}catch{$checks[$endpoint]=$false}}
  $checks.private_file_root_writable=Test-KgPrivateStorageWritable $s
  $privateKey="worker_kind='private_file'";$scannerKey='scanner_health';$scannerContainerKey='clamav_container';$otherFailures=@($checks.GetEnumerator()|Where-Object{(-not$_.Value)-and$_.Key-ne$privateKey-and$_.Key-ne$scannerKey-and$_.Key-ne$scannerContainerKey})
  if($otherFailures.Count-gt0){@{status='NOT_READY';code='KG_LOCAL_READINESS_INCOMPLETE';failed_checks=@($otherFailures.Name)}|ConvertTo-Json -Compress;exit 1}
  if(-not$checks[$privateKey]-or-not$checks[$scannerKey]-or-not$checks[$scannerContainerKey]){$capabilityFailures=@();foreach($key in @($privateKey,$scannerContainerKey,$scannerKey)){if(-not$checks[$key]){$capabilityFailures+=$key}};@{status='BLOCKED';code='KG_LOCAL_CAPABILITY_BLOCKED';failed_checks=$capabilityFailures;capability='FOUNDATION_ONLY';scanner='BLOCKED'}|ConvertTo-Json -Depth 5 -Compress;exit 1}
  @{status='READY';code='READY';checks=$checks;capability='FOUNDATION_ONLY';scanner='READY'}|ConvertTo-Json -Depth 5 -Compress
} catch {@{status='NOT_READY';code=if($_.Exception.Message-match'^KG_'){$_.Exception.Message}else{'KG_LOCAL_READINESS_INCOMPLETE'}}|ConvertTo-Json -Compress;exit 1}
