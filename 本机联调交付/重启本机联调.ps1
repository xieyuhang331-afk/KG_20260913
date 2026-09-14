. (Join-Path $PSScriptRoot '本机联调公共.ps1')
try{
  $beforeState=Read-KgState;$before=$beforeState.sentinel
  & (Join-Path $PSScriptRoot '停止本机联调.ps1')|Out-Null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_STOP_FAILED'}
  $startOutput=& (Join-Path $PSScriptRoot '启动本机联调.ps1');$startExit=$LASTEXITCODE
  $startResult=$startOutput|Select-Object -Last 1|ConvertFrom-Json
  if($startExit-ne0-and$startResult.code-ne'KG_LOCAL_CAPABILITY_BLOCKED'){Fail-Kg $startResult.code}
  $afterState=Read-KgState
  $fileSentinel=Get-Content -LiteralPath (Join-Path $afterState.state_dir '私有文件/.g1-persistence-sentinel') -Raw -Encoding ascii
  $databaseSentinel=& docker exec $afterState.database_container psql -v ON_ERROR_STOP=1 -At -U postgres -d $afterState.runtime.KG_DATABASE_NAME -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database();"
  if($LASTEXITCODE-ne0-or$before-ne$afterState.sentinel-or$before-ne$fileSentinel-or$databaseSentinel.Trim()-ne"kg-test-disposable:$($afterState.run_id)"){Fail-Kg 'KG_LOCAL_PERSISTENCE_FAILED'}
  $result=@{status=if($startExit-eq0){'READY'}else{'BLOCKED'};code=if($startExit-eq0){'READY'}else{'KG_LOCAL_CAPABILITY_BLOCKED'};persistence='VERIFIED';scanner=$startResult.scanner}
  $result|ConvertTo-Json -Compress
  if($startExit-ne0){exit 1}
}catch{@{status='FAILED';code=if($_.Exception.Message-match'^KG_'){$_.Exception.Message}else{'KG_LOCAL_PERSISTENCE_FAILED'}}|ConvertTo-Json -Compress;exit 1}
