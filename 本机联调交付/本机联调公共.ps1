Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:交付目录 = Split-Path -Parent $PSCommandPath
$script:仓库根 = Split-Path -Parent $script:交付目录
$script:状态根 = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) '康邻完整联调包V2'
$script:当前状态 = Join-Path $script:状态根 '当前状态.json'

function Fail-Kg([string]$Code) { throw $Code }
function New-KgRandomHex([int]$Bytes=24) {
  $buffer = [byte[]]::new($Bytes); [Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
  return [Convert]::ToHexString($buffer).ToLowerInvariant()
}
function New-KgRandomBase64([int]$Bytes=32) {
  $buffer = [byte[]]::new($Bytes); [Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
  return [Convert]::ToBase64String($buffer)
}
function Assert-KgNoReparsePath([string]$Path) {
  $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  while($item){
    if(($item.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne0){Fail-Kg 'KG_LOCAL_CLEANUP_SCOPE_INVALID'}
    $item=if($item-is[IO.FileInfo]){$item.Directory}else{$item.Parent}
  }
}
function Assert-KgPrivateAcl([string]$Path) {
  $acl=Get-Acl -LiteralPath $Path
  $unexpected=@($acl.Access|Where-Object{$_.AccessControlType-ne'Allow'-or$_.IdentityReference.Value-notmatch"(^|\\)$([regex]::Escape($env:USERNAME))$"})
  if(-not$acl.AreAccessRulesProtected-or$unexpected.Count-ne0){Fail-Kg 'KG_LOCAL_STATE_PERMISSION_INVALID'}
}
function Initialize-KgPrivateDirectory([string]$Path) {
  if(Test-Path -LiteralPath $Path){
    Assert-KgNoReparsePath $Path
    Assert-KgPrivateAcl $Path
    return
  }
  New-Item -ItemType Directory -Path $Path|Out-Null
  & icacls.exe $Path /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)(M)" *> $null
  if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_STATE_PERMISSION_INVALID'}
  Assert-KgPrivateAcl $Path
}
function Initialize-KgPrivateFile([string]$Path) {
  if(Test-Path -LiteralPath $Path){Fail-Kg 'KG_LOCAL_STATE_PERMISSION_INVALID'}
  [IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None).Dispose()
  & icacls.exe $Path /inheritance:r /grant:r "${env:USERNAME}:(M)" *> $null
  if($LASTEXITCODE-ne0){[IO.File]::Delete($Path);Fail-Kg 'KG_LOCAL_STATE_PERMISSION_INVALID'}
  try{Assert-KgPrivateAcl $Path}catch{[IO.File]::Delete($Path);throw}
}
function Read-KgState {
  if (-not (Test-Path -LiteralPath $script:当前状态 -PathType Leaf)) { Fail-Kg 'KG_LOCAL_STATE_MISSING' }
  Assert-KgNoReparsePath $script:当前状态
  Assert-KgPrivateAcl $script:状态根
  Assert-KgPrivateAcl $script:当前状态
  $state=Get-Content -LiteralPath $script:当前状态 -Raw -Encoding UTF8 | ConvertFrom-Json
  Assert-KgStateIdentity $state
  return $state
}
function Assert-KgStateIdentity($State) {
  $run=[string]$State.run_id
  if($State.schema_version-ne2-or$run-notmatch'^[0-9a-f]{16}$'){Fail-Kg 'KG_LOCAL_CLEANUP_SCOPE_INVALID'}
  if([string]$State.compose_project-ne"kg-g1-$run"){Fail-Kg 'KG_LOCAL_CLEANUP_SCOPE_INVALID'}
  $expectedStateDir=[IO.Path]::GetFullPath((Join-Path $script:状态根 $run))
  if([IO.Path]::GetFullPath([string]$State.state_dir)-ne$expectedStateDir){Fail-Kg 'KG_LOCAL_CLEANUP_SCOPE_INVALID'}
  if(-not(Test-Path -LiteralPath $expectedStateDir -PathType Container)){Fail-Kg 'KG_LOCAL_STATE_MISSING'}
  Assert-KgNoReparsePath $expectedStateDir
  Assert-KgPrivateAcl $expectedStateDir
}
function Write-KgState($State) {
  Initialize-KgPrivateDirectory $script:状态根
  $tmp = "$script:当前状态.tmp"
  Initialize-KgPrivateFile $tmp
  try{$State | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $tmp -Encoding utf8NoBOM;Move-Item -LiteralPath $tmp -Destination $script:当前状态 -Force}catch{[IO.File]::Delete($tmp);throw}
}
function Assert-KgInside([string]$Child,[string]$Parent) {
  $childPath=[IO.Path]::GetFullPath($Child); $parentPath=[IO.Path]::GetFullPath($Parent).TrimEnd('\')+'\'
  if (-not $childPath.StartsWith($parentPath,[StringComparison]::OrdinalIgnoreCase)) { Fail-Kg 'KG_LOCAL_CLEANUP_SCOPE_INVALID' }
  return $childPath
}
function Get-KgSource {
  $git = Join-Path $script:仓库根 '.git'
  if (Test-Path -LiteralPath $git) {
    $dirty=git -C $script:仓库根 status --porcelain=v1
    if($LASTEXITCODE-ne0-or$dirty){Fail-Kg 'KG_PACKAGE_SOURCE_INVALID'}
    $head=(git -C $script:仓库根 rev-parse HEAD).Trim(); if($LASTEXITCODE-ne0){Fail-Kg 'KG_PACKAGE_SOURCE_INVALID'}
    $tree=(git -C $script:仓库根 rev-parse 'HEAD^{tree}').Trim(); if($LASTEXITCODE-ne0){Fail-Kg 'KG_PACKAGE_SOURCE_INVALID'}
    return @{mode='worktree';head=$head;tree=$tree}
  }
  $manifestPath=Join-Path $script:交付目录 '完整联调包源码清单.json'
  if(-not(Test-Path -LiteralPath $manifestPath -PathType Leaf)){Fail-Kg 'KG_PACKAGE_SOURCE_INVALID'}
  $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8|ConvertFrom-Json
  if($manifest.schema_version -ne 2 -or $manifest.distribution_policy_version -ne 'KG_G1_PROJECTION_V1'){Fail-Kg 'KG_PACKAGE_SOURCE_INVALID'}
  $included=@($manifest.included_files);$excluded=@($manifest.excluded_paths)
  if($manifest.source_file_count-ne($included.Count+$excluded.Count)-or$manifest.source_object_count-ne$manifest.source_file_count){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}
  foreach($digest in @($manifest.openapi_sha256,$manifest.uv_lock_sha256,$manifest.frontend_lock_sha256)){if([string]$digest-notmatch'^[0-9A-F]{64}$'){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}}
  if([string]$manifest.migration_head-notmatch'^\d{8}_\d{4}$'-or-not$manifest.tool_versions){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}
  foreach($entry in $included){
    if($entry.path -match '(^[A-Za-z]:)|(^/)|(\.\.)'){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}
    $path=Join-Path $script:仓库根 $entry.path
    if(-not(Test-Path -LiteralPath $path -PathType Leaf)){Fail-Kg 'KG_PACKAGE_SOURCE_INVALID'}
    if([string]$entry.git_blob-notmatch'^[0-9a-f]{40}$'-or$entry.mode-notin@('100644','100755')){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}
    if((Get-Item -LiteralPath $path).Length -ne $entry.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}
  }
  if((Get-FileHash -LiteralPath (Join-Path $script:仓库根 'backend/uv.lock') -Algorithm SHA256).Hash-ne$manifest.uv_lock_sha256-or(Get-FileHash -LiteralPath (Join-Path $script:仓库根 'frontend/package-lock.json') -Algorithm SHA256).Hash-ne$manifest.frontend_lock_sha256){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}
  foreach($entry in $excluded){if($entry.path -match '(^[A-Za-z]:)|(^/)|(\.\.)' -or (Test-Path -LiteralPath (Join-Path $script:仓库根 $entry.path))){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}}
  $manifestRelative='本机联调交付/完整联调包源码清单.json'
  $runtimeGenerated='^(backend/\.venv/|frontend/node_modules/|(?:.*/)?__pycache__/|\.pytest_cache/|\.ruff_cache/)|\.(pyc|pyo)$'
  $actual=@(Get-ChildItem -LiteralPath $script:仓库根 -Recurse -File|ForEach-Object{[IO.Path]::GetRelativePath($script:仓库根,$_.FullName).Replace('\','/')}|Where-Object{$_-ne$manifestRelative-and$_-notmatch$runtimeGenerated}|Sort-Object)
  $expected=@($included.path|Sort-Object)
  if(Compare-Object -ReferenceObject $expected -DifferenceObject $actual){Fail-Kg 'KG_PACKAGE_MANIFEST_MISMATCH'}
  return @{mode='package';head=$manifest.source_commit;tree=$manifest.source_tree;migration_head=$manifest.migration_head;openapi_sha256=$manifest.openapi_sha256}
}
function Get-KgComposeArgs($State){Assert-KgStateIdentity $State;@('compose','-f',(Join-Path $script:交付目录 '本机联调容器.yml'),'--project-name',$State.compose_project)}
function Assert-KgOwnedResources($State) {
  Assert-KgStateIdentity $State
  $run=[string]$State.run_id
  if($run-notmatch'^[0-9a-f]{16}$'){Fail-Kg 'KG_LOCAL_CLEANUP_SCOPE_INVALID'}
  $expected=[ordered]@{
    database_container="kg-g1-db-$run";rabbitmq_container="kg-g1-rabbit-$run";clamav_container="kg-g1-clamd-$run"
    database_volume="kg-g1-db-data-$run";rabbitmq_volume="kg-g1-rabbit-data-$run";clamav_config_volume="kg-g1-clamd-config-$run";clamav_signature_volume="kg-g1-clamd-signatures-$run"
    network_name="kg-g1-net-$run"
  }
  foreach($entry in $expected.GetEnumerator()){if([string]$State.($entry.Key)-ne$entry.Value){Fail-Kg 'KG_LOCAL_CLEANUP_SCOPE_INVALID'}}
  $targets=@(
    @('container',$State.database_container),@('container',$State.rabbitmq_container),@('container',$State.clamav_container),
    @('volume',$State.database_volume),@('volume',$State.rabbitmq_volume),@('volume',$State.clamav_config_volume),@('volume',$State.clamav_signature_volume),
    @('network',$State.network_name)
  )
  foreach($target in $targets){
    $kind=$target[0];$name=$target[1]
    if($kind-eq'container'){$listed=@(docker container ls -a --filter "name=^/$name$" --format '{{.Names}}' 2>$null)}
    elseif($kind-eq'volume'){$listed=@(docker volume ls --filter "name=^$name$" --format '{{.Name}}' 2>$null)}
    else{$listed=@(docker network ls --filter "name=^$name$" --format '{{.Name}}' 2>$null)}
    if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_RESOURCE_OWNERSHIP_UNKNOWN'}
    if($listed.Count-eq0){continue}
    if($listed.Count-ne1-or$listed[0]-ne$name){Fail-Kg 'KG_LOCAL_RESOURCE_OWNERSHIP_UNKNOWN'}
    if($kind-eq'container'){$label=docker inspect $name --format '{{index .Config.Labels "kg.g1.delivery"}}' 2>$null}
    elseif($kind-eq'volume'){$label=docker volume inspect $name --format '{{index .Labels "kg.g1.delivery"}}' 2>$null}
    else{$label=docker network inspect $name --format '{{index .Labels "kg.g1.delivery"}}' 2>$null}
    if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_RESOURCE_OWNERSHIP_UNKNOWN'}
    if($label-ne$run){Fail-Kg 'KG_LOCAL_CLEANUP_SCOPE_INVALID'}
  }
}
function Test-KgPrivateStorageWritable($State) {
  Assert-KgStateIdentity $State
  $root=[IO.Path]::GetFullPath((Join-Path $State.state_dir '私有文件'))
  if(-not(Test-Path -LiteralPath $root -PathType Container)){return $false}
  try{Assert-KgNoReparsePath $root}catch{return $false}
  $probe=Join-Path $root ".$($State.run_id)-$(New-KgRandomHex 8).probe"
  try{
    $stream=[IO.File]::Open($probe,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.WriteByte(71)}finally{$stream.Dispose()}
    return $true
  }catch{return $false}finally{if(Test-Path -LiteralPath $probe -PathType Leaf){[IO.File]::Delete($probe)}}
}
function Assert-KgClamdConfigVolume($State){
  foreach($volume in @($State.clamav_config_volume,$State.clamav_signature_volume)){
    $label=docker volume inspect $volume --format '{{index .Labels "kg.g1.delivery"}}' 2>$null
    if($LASTEXITCODE-ne0-or$label-ne$State.run_id){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
  }
  $image='clamav/clamav@sha256:f156095071757e3838caa50265d65e36cdf7f934a27aacf851ea6d2fadbe8200'
  $hashOutput=docker run --rm --network none --memory 64m --mount "type=volume,source=$($State.clamav_config_volume),target=/g1-config,readonly" --entrypoint sha256sum $image /g1-config/clamd.conf 2>$null
  if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
  $actualHash=(([string]$hashOutput)-split '\s+')[0].ToUpperInvariant()
  if($actualHash-ne$State.clamav_config_sha256){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
}
function Wait-KgHealthy([string]$Name,[int]$Seconds=90){
  $until=(Get-Date).AddSeconds($Seconds); do { $s=docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $Name 2>$null; if($LASTEXITCODE-eq0-and$s-eq'healthy'){return}; Start-Sleep 2 } while((Get-Date)-lt$until)
  Fail-Kg 'KG_LOCAL_CONTAINER_NOT_READY'
}
function Test-KgProcessIdentity($Record) {
  $process=Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
  if(-not$process){return $false}
  try{if($process.StartTime.ToUniversalTime().Ticks-ne[long]$Record.created_at_utc_ticks){return $false}}catch{return $false}
  $native=Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$Record.pid)" -ErrorAction SilentlyContinue
  return [bool]($native-and$native.CommandLine-and$native.CommandLine.Contains([string]$Record.identity_marker,[StringComparison]::Ordinal))
}
function Invoke-KgTaskkill([int]$ProcessId) {
  & taskkill.exe /PID $ProcessId /T /F 2>$null|Out-Null
  return $LASTEXITCODE
}
function Stop-KgOwnedProcess($Record) {
  if(-not(Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue)){return}
  if(-not(Test-KgProcessIdentity $Record)){Fail-Kg 'KG_LOCAL_PROCESS_OWNERSHIP_INVALID'}
  if((Invoke-KgTaskkill ([int]$Record.pid))-ne0){Fail-Kg 'KG_LOCAL_PROCESS_STOP_FAILED'}
}
function Stop-KgOwnedProcesses($Processes) {
  $firstFailure=$null
  foreach($processEntry in $Processes.psobject.Properties){try{Stop-KgOwnedProcess $processEntry.Value}catch{if(-not$firstFailure){$firstFailure=$_.Exception.Message}}}
  if($firstFailure){Fail-Kg $firstFailure}
}
function Start-KgHidden($State,[string]$Name,[string]$Directory,[string]$Command){
  $logDir=Assert-KgInside (Join-Path $State.state_dir '日志') $script:状态根; New-Item -ItemType Directory -Force -Path $logDir|Out-Null
  $marker="g1-$($State.run_id)-$Name-$(New-KgRandomHex 8)";$wrapped="`$env:KG_G1_PROCESS_IDENTITY='$marker';$Command";$p=$null;$record=$null
  try{
    $p=Start-Process pwsh.exe -WindowStyle Hidden -WorkingDirectory $Directory -ArgumentList @('-NoProfile','-Command',$wrapped) -RedirectStandardOutput (Join-Path $logDir "$Name.out.log") -RedirectStandardError (Join-Path $logDir "$Name.err.log") -PassThru
    $record=[ordered]@{pid=$p.Id;created_at_utc_ticks=$p.StartTime.ToUniversalTime().Ticks;identity_marker=$marker}
    $State.processes | Add-Member -NotePropertyName $Name -NotePropertyValue $record -Force
    Write-KgState $State
  }catch{
    $primary=$_
    if($record){try{Stop-KgOwnedProcess $record}catch{Fail-Kg 'KG_LOCAL_PROCESS_STOP_FAILED'}}
    throw $primary
  }
}
