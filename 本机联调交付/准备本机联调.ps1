. (Join-Path $PSScriptRoot '本机联调公共.ps1')
$state=$null
try {
  if(Test-Path -LiteralPath $script:当前状态){Fail-Kg 'KG_LOCAL_STATE_ALREADY_EXISTS_USE_EXPLICIT_RESET'}
  & (Join-Path $PSScriptRoot '前置软件检查.ps1')|Out-Null; if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_PREREQUISITE_FAILED'}
  $source=Get-KgSource; $run=New-KgRandomHex 8; $stateDir=Join-Path $script:状态根 $run
  Initialize-KgPrivateDirectory $script:状态根
  Initialize-KgPrivateDirectory $stateDir
  $sourceClamdConfig=Join-Path $script:仓库根 'backend/clamd私有文件扫描_V1.conf'
  if(-not(Test-Path -LiteralPath $sourceClamdConfig -PathType Leaf)){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
  $clamdText=[IO.File]::ReadAllText($sourceClamdConfig,[Text.Encoding]::UTF8).Replace("`r`n","`n").Replace("`r","`n");$clamdBytes=[Text.Encoding]::UTF8.GetBytes($clamdText);$clamdConfigBase64=[Convert]::ToBase64String($clamdBytes);$clamdConfigHash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($clamdBytes))
  $state=@{schema_version=2;run_id=$run;source=$source;state_dir=$stateDir;compose_project="kg-g1-$run";database_container="kg-g1-db-$run";rabbitmq_container="kg-g1-rabbit-$run";clamav_container="kg-g1-clamd-$run";database_volume="kg-g1-db-data-$run";rabbitmq_volume="kg-g1-rabbit-data-$run";network_name="kg-g1-net-$run";clamav_signature_volume="kg-g1-clamd-signatures-$run";clamav_config_volume="kg-g1-clamd-config-$run";clamav_config_sha256=$clamdConfigHash;database_port=55432;rabbitmq_port=56792;clamav_port=33310;api_port=8000;platform_port=5173;institution_port=5174;processes=@{};worker_hosts=@{};sentinel=(New-KgRandomHex 16)}
  $env:KG_LOCAL_RUN_ID=$run;$env:KG_LOCAL_DATABASE_CONTAINER=$state.database_container;$env:KG_LOCAL_RABBITMQ_CONTAINER=$state.rabbitmq_container;$env:KG_LOCAL_CLAMD_CONTAINER=$state.clamav_container;$env:KG_LOCAL_DATABASE_VOLUME=$state.database_volume;$env:KG_LOCAL_RABBITMQ_VOLUME=$state.rabbitmq_volume;$env:KG_LOCAL_NETWORK=$state.network_name;$env:KG_LOCAL_CLAMD_SIGNATURE_VOLUME=$state.clamav_signature_volume;$env:KG_LOCAL_CLAMD_CONFIG_VOLUME=$state.clamav_config_volume;$env:KG_LOCAL_DATABASE_PORT=$state.database_port;$env:KG_LOCAL_RABBITMQ_PORT=$state.rabbitmq_port;$env:KG_LOCAL_CLAMD_PORT=$state.clamav_port;$env:KG_LOCAL_POSTGRES_PASSWORD=New-KgRandomHex 24;$env:KG_LOCAL_RABBITMQ_USER="kg_$run";$env:KG_LOCAL_RABBITMQ_PASSWORD=New-KgRandomHex 24
  $state.runtime=@{KG_LOCAL_POSTGRES_PASSWORD=$env:KG_LOCAL_POSTGRES_PASSWORD;KG_LOCAL_RABBITMQ_USER=$env:KG_LOCAL_RABBITMQ_USER;KG_LOCAL_RABBITMQ_PASSWORD=$env:KG_LOCAL_RABBITMQ_PASSWORD}
  $clamavImage='clamav/clamav@sha256:f156095071757e3838caa50265d65e36cdf7f934a27aacf851ea6d2fadbe8200'
  docker volume create --label "kg.g1.delivery=$run" $state.clamav_signature_volume *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
  docker volume create --label "kg.g1.delivery=$run" $state.clamav_config_volume *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
  docker run --rm --network none --memory 128m -e "G1_CLAMD_CONFIG_B64=$clamdConfigBase64" --mount "type=volume,source=$($state.clamav_config_volume),target=/g1-config" --entrypoint sh $clamavImage -c 'printf %s "$G1_CLAMD_CONFIG_B64" | base64 -d >/g1-config/clamd.conf' *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
  Assert-KgClamdConfigVolume $state
  docker run --rm --network none --memory 256m --mount "type=volume,source=$($state.clamav_signature_volume),target=/s1-signatures" --entrypoint sh $clamavImage -c 'cp -a /var/lib/clamav/. /s1-signatures/' *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
  docker run --rm --memory 512m --mount "type=volume,source=$($state.clamav_signature_volume),target=/var/lib/clamav" --entrypoint /usr/bin/freshclam $clamavImage --datadir=/var/lib/clamav --stdout --no-warnings *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_SCANNER_CONFIGURATION_INVALID'}
  $args=Get-KgComposeArgs $state; & docker @args up -d; if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_CONTAINER_NOT_READY'}
  Wait-KgHealthy $state.database_container; Wait-KgHealthy $state.rabbitmq_container; Wait-KgHealthy $state.clamav_container 180
  & uvx --from uv==0.12.7 uv sync --frozen --all-groups --python 3.11.16 --project (Join-Path $script:仓库根 'backend'); if($LASTEXITCODE-ne0){Fail-Kg 'KG_PACKAGE_LOCK_INVALID'}
  Push-Location (Join-Path $script:仓库根 'frontend'); try { npm ci; if($LASTEXITCODE-ne0){Fail-Kg 'KG_PACKAGE_LOCK_INVALID'} } finally { Pop-Location }
  $databaseName="kg_it_$run";$rolePassword=New-KgRandomHex 24;$backend=Join-Path $script:仓库根 'backend'
  $sources=@(Get-ChildItem -LiteralPath (Join-Path $backend 'app') -Recurse -Filter '*.py'|ForEach-Object{$_.FullName})
  $allText=($sources|ForEach-Object{Get-Content -LiteralPath $_ -Raw -Encoding UTF8})-join"`n"
  $names=[regex]::Matches($allText,'KG_[A-Z0-9_]+')|ForEach-Object{$_.Value}|Sort-Object -Unique
  $roleVars=@($names|Where-Object{($_-match'_ROLE$'-or$_-in@('KG_DATABASE_USER','KG_READONLY_ROLE'))-and$_-notin@('KG_TEST_APPLICATION_ROLE','KG_TEST_DELIVERY_WORKER_ROLE')})
  $roleMap=@{};$index=0;foreach($name in $roleVars){$index++;$role="kg_g1_r$index`_$run";$roleMap[$name]=$role;[Environment]::SetEnvironmentVariable($name,$role,'Process');$state.runtime[$name]=$role}
  $sql=@('SET client_min_messages=warning;')
  foreach($role in $roleMap.Values){$sql+="CREATE ROLE `"$role`" LOGIN PASSWORD '$rolePassword' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;"}
  $sql+="CREATE DATABASE `"$databaseName`";";$sql+="COMMENT ON DATABASE `"$databaseName`" IS 'kg-test-disposable:$run';";$sqlPath=Join-Path $stateDir 'provision.sql'
  Initialize-KgPrivateFile $sqlPath
  try{$sql|Set-Content -LiteralPath $sqlPath -Encoding utf8NoBOM;Get-Content -LiteralPath $sqlPath -Raw|docker exec -i $state.database_container psql -v ON_ERROR_STOP=1 -U postgres -d postgres *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_ROLE_PROVISION_FAILED'}}finally{[IO.File]::Delete($sqlPath)}
  $escaped=[uri]::EscapeDataString($rolePassword);$postgresEscaped=[uri]::EscapeDataString($env:KG_LOCAL_POSTGRES_PASSWORD)
  foreach($urlName in @($names|Where-Object{$_-match'_DATABASE_URL$'})){$roleName=$urlName-replace'_DATABASE_URL$','_ROLE';if($urlName-eq'KG_IDENTITY_APPLICATION_DATABASE_URL'){$roleName='KG_DATABASE_USER'}elseif($urlName-eq'KG_READONLY_DATABASE_URL'){$roleName='KG_READONLY_ROLE'};$role=$roleMap[$roleName];if(-not$role){Fail-Kg 'KG_LOCAL_ROLE_MAPPING_UNKNOWN'};$url="postgresql+asyncpg://$role`:$escaped@127.0.0.1:$($state.database_port)/$databaseName";[Environment]::SetEnvironmentVariable($urlName,$url,'Process');$state.runtime[$urlName]=$url}
  $adminUrl="postgresql+asyncpg://postgres:$postgresEscaped@127.0.0.1:$($state.database_port)/$databaseName";[Environment]::SetEnvironmentVariable('KG_DATABASE_URL',$adminUrl,'Process')
  foreach($name in @($names|Where-Object{$_-match'(SECRET|PEPPER|HMAC|SIGNING_KEY|KEK_B64|KEYRING_JSON|CURRENT_KEY_ID|_KEY_ID$|_KEY_B64$)'})){
    if($name-match'KEYRING_JSON$'){continue};$value=if($name-match'(_B64|HMAC_KEY_B64)$'){New-KgRandomBase64 32}elseif($name-match'(CURRENT_KEY_ID|_KEY_ID)$'){"g1-$((New-KgRandomHex 8))"}else{New-KgRandomHex 32};[Environment]::SetEnvironmentVariable($name,$value,'Process');$state.runtime[$name]=$value
  }
  foreach($keyring in @($names|Where-Object{$_-match'KEYRING_JSON$'})){$idName=$keyring-replace'KEYRING_JSON$','CURRENT_KEY_ID';$id=[Environment]::GetEnvironmentVariable($idName);if(-not$id){$id="g1-$((New-KgRandomHex 8))";[Environment]::SetEnvironmentVariable($idName,$id,'Process');$state.runtime[$idName]=$id};$json=@{$id=(New-KgRandomBase64 32)}|ConvertTo-Json -Compress;[Environment]::SetEnvironmentVariable($keyring,$json,'Process');$state.runtime[$keyring]=$json}
  $env:KG_DATABASE_NAME=$databaseName;$env:KG_DATABASE_HOST='127.0.0.1';$env:KG_DATABASE_PORT=[string]$state.database_port;$state.runtime.KG_DATABASE_NAME=$databaseName;$state.runtime.KG_DATABASE_HOST='127.0.0.1';$state.runtime.KG_DATABASE_PORT=[string]$state.database_port
  $env:KG_DATABASE_PASSWORD=$rolePassword;$state.runtime.KG_DATABASE_PASSWORD=$rolePassword
  if(-not$roleMap.KG_DATABASE_USER-or-not$roleMap.KG_DELIVERY_WORKER_ROLE){Fail-Kg 'KG_LOCAL_ROLE_PROVISION_FAILED'}
  $env:KG_TEST_ENVIRONMENT='local_disposable';$env:KG_TEST_RUN_ID=$run;$env:KG_TEST_APPLICATION_ROLE=$roleMap.KG_DATABASE_USER;$env:KG_TEST_DELIVERY_WORKER_ROLE=$roleMap.KG_DELIVERY_WORKER_ROLE
  $state.runtime.KG_TEST_ENVIRONMENT='local_disposable';$state.runtime.KG_TEST_RUN_ID=$run;$state.runtime.KG_TEST_APPLICATION_ROLE=$roleMap.KG_DATABASE_USER;$state.runtime.KG_TEST_DELIVERY_WORKER_ROLE=$roleMap.KG_DELIVERY_WORKER_ROLE
  Push-Location $backend;try{& (Join-Path $backend '.venv/Scripts/python.exe') -c "from alembic.config import Config; from alembic import command; import os; c=Config('alembic.ini'); c.set_main_option('sqlalchemy.url',os.environ['KG_DATABASE_URL']); command.upgrade(c,'head')" *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_MIGRATION_FAILED'};$head=& (Join-Path $backend '.venv/Scripts/python.exe') -c "from alembic.config import Config; from alembic.script import ScriptDirectory; c=Config('alembic.ini'); print(ScriptDirectory.from_config(c).get_current_head())" 2>$null;if($LASTEXITCODE-ne0-or-not$head){Fail-Kg 'KG_LOCAL_MIGRATION_FAILED'}}finally{Pop-Location;[Environment]::SetEnvironmentVariable('KG_DATABASE_URL',$null,'Process')};$state.migration_head=$head.Trim()
  $privateRoot=Join-Path $stateDir '私有文件';New-Item -ItemType Directory -Force -Path $privateRoot|Out-Null
  Set-Content -LiteralPath (Join-Path $privateRoot '.g1-persistence-sentinel') -Value $state.sentinel -Encoding ascii -NoNewline
  $state.runtime.KG_PRIVATE_FILE_SCANNER_FACTORY='app.modules.private_file.clamav_scanner:build_clamav_scanner'
  $state.runtime.KG_PRIVATE_FILE_CLAMD_HOST='127.0.0.1';$state.runtime.KG_PRIVATE_FILE_CLAMD_PORT=[string]$state.clamav_port
  $state.runtime.KG_PRIVATE_FILE_SCANNER_ENGINE_VERSION='1.4.6';$state.runtime.KG_PRIVATE_FILE_SCANNER_TIMEOUT_SECONDS='30';$state.runtime.KG_PRIVATE_FILE_SCANNER_MAX_SIGNATURE_AGE_HOURS='48'
  Write-KgState $state
  @{status='PREPARED';code='PREPARED';run_id=$run}|ConvertTo-Json -Compress
} catch {
  $failureCode=if($_.Exception.Message-match'^KG_'){$_.Exception.Message}else{'KG_LOCAL_PREPARE_FAILED'}
  if($state){
    $env:KG_LOCAL_RUN_ID=$state.run_id;$env:KG_LOCAL_DATABASE_CONTAINER=$state.database_container;$env:KG_LOCAL_RABBITMQ_CONTAINER=$state.rabbitmq_container;$env:KG_LOCAL_CLAMD_CONTAINER=$state.clamav_container;$env:KG_LOCAL_DATABASE_VOLUME=$state.database_volume;$env:KG_LOCAL_RABBITMQ_VOLUME=$state.rabbitmq_volume;$env:KG_LOCAL_NETWORK=$state.network_name;$env:KG_LOCAL_CLAMD_SIGNATURE_VOLUME=$state.clamav_signature_volume;$env:KG_LOCAL_CLAMD_CONFIG_VOLUME=$state.clamav_config_volume;$env:KG_LOCAL_DATABASE_PORT=$state.database_port;$env:KG_LOCAL_RABBITMQ_PORT=$state.rabbitmq_port;$env:KG_LOCAL_CLAMD_PORT=$state.clamav_port
    try{Assert-KgOwnedResources $state;$args=Get-KgComposeArgs $state;& docker @args down --volumes *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_PREPARE_CLEANUP_FAILED'};$failedStateDir=Assert-KgInside $state.state_dir $script:状态根;if(Test-Path -LiteralPath $failedStateDir){[IO.Directory]::Delete($failedStateDir,$true)}}catch{$failureCode=if($_.Exception.Message-match'^KG_'){$_.Exception.Message}else{'KG_LOCAL_PREPARE_CLEANUP_FAILED'}}
  }
  @{status='FAILED';code=$failureCode}|ConvertTo-Json -Compress; exit 1
}
