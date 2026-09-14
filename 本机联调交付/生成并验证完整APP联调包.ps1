param(
  [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$Commit,
  [Parameter(Mandatory=$true)][string]$OutputDirectory,
  [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$OpenApiSha256
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$approvedExclusion='frontend/验收证据/组织前端原型对齐V1/前端测试结果.xml'
function Fail-Package([string]$Code){throw $Code}
try {
  if((git -C $root cat-file -t $Commit).Trim()-ne'commit'){Fail-Package 'KG_PACKAGE_SOURCE_INVALID'}
  $tree=(git -C $root rev-parse "$Commit`^{tree}").Trim()
  if($LASTEXITCODE-ne0){Fail-Package 'KG_PACKAGE_SOURCE_INVALID'}
  $out=[IO.Path]::GetFullPath($OutputDirectory)
  if(Test-Path -LiteralPath $out){Fail-Package 'KG_PACKAGE_OUTPUT_ALREADY_EXISTS'}
  $parent=Split-Path -Parent $out
  if(-not(Test-Path -LiteralPath $parent -PathType Container)){Fail-Package 'KG_PACKAGE_OUTPUT_PARENT_MISSING'}
  New-Item -ItemType Directory -Path $out|Out-Null
  $ownershipValue=[Guid]::NewGuid().ToString('N');$ownershipPath=Join-Path $out '.kg-g1-package-owner'
  $ownershipStream=[IO.File]::Open($ownershipPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$ownershipBytes=[Text.Encoding]::ASCII.GetBytes($ownershipValue);$ownershipStream.Write($ownershipBytes,0,$ownershipBytes.Length)}finally{$ownershipStream.Dispose()}
  $stage=Join-Path $out "stage-$($Commit.Substring(0,12))"
  New-Item -ItemType Directory -Path $stage|Out-Null

  $lsTreeRaw=git -C $root ls-tree -r -z --full-tree $Commit
  if($LASTEXITCODE-ne0){Fail-Package 'KG_PACKAGE_SOURCE_INVALID'}
  $objects=@()
  foreach($record in ([string]$lsTreeRaw).Split([char]0,[StringSplitOptions]::RemoveEmptyEntries)){
    if($record-notmatch'^(?<mode>\d{6}) (?<type>\w+) (?<oid>[0-9a-f]{40})\t(?<path>.+)$'){Fail-Package 'KG_PACKAGE_UNSUPPORTED_GIT_OBJECT'}
    if($Matches.type-ne'blob'-or$Matches.mode-notin@('100644','100755')){Fail-Package 'KG_PACKAGE_UNSUPPORTED_GIT_OBJECT'}
    $objects+=,[ordered]@{mode=$Matches.mode;oid=$Matches.oid;path=$Matches.path}
  }
  if(-not$objects){Fail-Package 'KG_PACKAGE_SOURCE_INVALID'}
  $pathKeys=@($objects|ForEach-Object{$_.path.ToLowerInvariant()})
  if(($pathKeys|Sort-Object -Unique).Count-ne$pathKeys.Count){Fail-Package 'KG_PACKAGE_PATH_COLLISION'}

  $archive=Join-Path $out 'source.zip'
  git -C $root archive --format=zip $Commit -o $archive
  if($LASTEXITCODE-ne0){Fail-Package 'KG_PACKAGE_ARCHIVE_FAILED'}
  try{Expand-Archive -LiteralPath $archive -DestinationPath $stage}catch{Fail-Package 'KG_PACKAGE_ARCHIVE_FAILED'}
  [IO.File]::Delete($archive)
  $stageBoundary=[IO.Path]::GetFullPath($stage).TrimEnd('\')+'\'
  $excluded=@()
  $approvedPath=[IO.Path]::GetFullPath((Join-Path $stage $approvedExclusion))
  if(-not$approvedPath.StartsWith($stageBoundary,[StringComparison]::OrdinalIgnoreCase)){Fail-Package 'KG_PACKAGE_CLEANUP_SCOPE_INVALID'}
  if(Test-Path -LiteralPath $approvedPath -PathType Leaf){
    $approvedItem=Get-Item -LiteralPath $approvedPath -Force
    if(($approvedItem.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne0){Fail-Package 'KG_PACKAGE_UNSUPPORTED_GIT_OBJECT'}
    $excluded+=,[ordered]@{path=$approvedExclusion;bytes=$approvedItem.Length;sha256=(Get-FileHash -LiteralPath $approvedPath -Algorithm SHA256).Hash;reason='TRACKED_HISTORICAL_JUNIT_EXCLUDED'}
    [IO.File]::Delete($approvedPath)
  }
  $allItems=@(Get-ChildItem -LiteralPath $stage -Recurse -Force)
  $forbidden=@($allItems|Where-Object{($_.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne0-or$_.Name-in@('.git','.venv','node_modules','dist','__pycache__','.env')-or$_.Extension-eq'.log'})
  $unapprovedJUnit=@($allItems|Where-Object{(-not$_.PSIsContainer)-and$_.Extension-eq'.xml'}|Where-Object{(Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8)-match'<testsuites?\b'})
  if($forbidden-or$unapprovedJUnit){Fail-Package 'KG_PACKAGE_FORBIDDEN_ARTIFACT'}

  $expectedObjects=@($objects|Where-Object{$_.path-ne$approvedExclusion}|Sort-Object path)
  $actualPaths=@(Get-ChildItem -LiteralPath $stage -Recurse -File|ForEach-Object{[IO.Path]::GetRelativePath($stage,$_.FullName).Replace('\','/')}|Sort-Object)
  if(Compare-Object -ReferenceObject @($expectedObjects.path) -DifferenceObject $actualPaths){Fail-Package 'KG_PACKAGE_MANIFEST_MISMATCH'}
  $rows=@($expectedObjects|ForEach-Object{
    $path=Join-Path $stage $_.path;$item=Get-Item -LiteralPath $path -Force
    if(($item.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne0){Fail-Package 'KG_PACKAGE_UNSUPPORTED_GIT_OBJECT'}
    $gitBytes=[int64](git -C $root cat-file -s $_.oid)
    $actualOid=(git -C $root hash-object --no-filters -- $path).Trim()
    if($LASTEXITCODE-ne0-or$item.Length-ne$gitBytes-or$actualOid-ne$_.oid){Fail-Package 'KG_PACKAGE_MANIFEST_MISMATCH'}
    [ordered]@{path=$_.path;mode=$_.mode;git_blob=$_.oid;bytes=$item.Length;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash}
  })
  $migrationFiles=Get-ChildItem -LiteralPath (Join-Path $stage 'backend/app/migrations/versions') -Filter '*.py'
  $revisions=@();$downRevisions=@()
  foreach($migration in $migrationFiles){$text=Get-Content -LiteralPath $migration.FullName -Raw -Encoding UTF8;if($text-match'(?m)^revision(?:\s*:\s*[^=]+)?\s*=\s*["'']([^"'']+)["'']'){$revisions+=$Matches[1]};if($text-match'(?m)^down_revision(?:\s*:\s*[^=]+)?\s*=\s*["'']([^"'']+)["'']'){$downRevisions+=$Matches[1]}}
  $heads=@($revisions|Where-Object{$_-notin$downRevisions}|Sort-Object -Unique)
  if($heads.Count-ne1){Fail-Package 'KG_PACKAGE_MIGRATION_HEAD_INVALID'}
  $migrationHead=$heads[0]
  $uvLock=Join-Path $stage 'backend/uv.lock';$frontendLock=Join-Path $stage 'frontend/package-lock.json'
  foreach($lock in @($uvLock,$frontendLock)){if(-not(Test-Path -LiteralPath $lock -PathType Leaf)){Fail-Package 'KG_PACKAGE_LOCK_INVALID'}}
  $python=Join-Path $root 'backend/.venv/Scripts/python.exe'
  if(-not(Test-Path -LiteralPath $python -PathType Leaf)){Fail-Package 'KG_PACKAGE_LOCK_INVALID'}
  $openApiArtifact=Join-Path $out "openapi_$($Commit.Substring(0,12)).json"
  $openApiProcess=[Diagnostics.ProcessStartInfo]::new();$openApiProcess.FileName=$python;$openApiProcess.WorkingDirectory=(Join-Path $stage 'backend');$openApiProcess.UseShellExecute=$false;$openApiProcess.CreateNoWindow=$true;$openApiProcess.RedirectStandardOutput=$true;$openApiProcess.RedirectStandardError=$true
  foreach($name in @($openApiProcess.Environment.Keys)){if($name.StartsWith('KG_',[StringComparison]::Ordinal)){$openApiProcess.Environment.Remove($name)}}
  $openApiProcess.Environment['PYTHONDONTWRITEBYTECODE']='1';$openApiProcess.Environment['KG_ENV']='local';$openApiProcess.Environment['KG_DATABASE_HOST']='127.0.0.1';$openApiProcess.Environment['KG_G1_OPENAPI_OUTPUT']=$openApiArtifact
  $seed=0;foreach($name in @('KG_DATABASE_PASSWORD','KG_JWT_SECRET_KEY','KG_AUTH_RATE_LIMIT_HMAC_KEY','KG_SLICE5_CURSOR_SIGNING_KEY','KG_SLICE7_CURSOR_SIGNING_KEY','KG_SLICE5_PUBLIC_REFERENCE_HMAC_KEY','KG_PRIVATE_FILE_ACCESS_SIGNING_KEY')){$seed++;$openApiProcess.Environment[$name]=(([char](96+$seed)).ToString()*40)}
  [void]$openApiProcess.ArgumentList.Add('-B');[void]$openApiProcess.ArgumentList.Add('-c');[void]$openApiProcess.ArgumentList.Add("import json,os; from pathlib import Path; from app.main import create_app; Path(os.environ['KG_G1_OPENAPI_OUTPUT']).write_bytes(json.dumps(create_app().openapi(),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8'))")
  $openApiRunner=[Diagnostics.Process]::new();$openApiRunner.StartInfo=$openApiProcess
  try{if(-not$openApiRunner.Start()){Fail-Package 'KG_PACKAGE_OPENAPI_INVALID'};$stdoutTask=$openApiRunner.StandardOutput.ReadToEndAsync();$stderrTask=$openApiRunner.StandardError.ReadToEndAsync();if(-not$openApiRunner.WaitForExit(120000)){$openApiRunner.Kill($true);$openApiRunner.WaitForExit();Fail-Package 'KG_PACKAGE_OPENAPI_INVALID'};[void]$stdoutTask.GetAwaiter().GetResult();[void]$stderrTask.GetAwaiter().GetResult();if($openApiRunner.ExitCode-ne0){Fail-Package 'KG_PACKAGE_OPENAPI_INVALID'}}finally{$openApiRunner.Dispose()}
  $actualOpenApiSha256=(Get-FileHash -LiteralPath $openApiArtifact -Algorithm SHA256).Hash
  if($actualOpenApiSha256-ne$OpenApiSha256.ToUpperInvariant()){Fail-Package 'KG_PACKAGE_OPENAPI_INVALID'}
  $toolVersions=[ordered]@{pwsh=$PSVersionTable.PSVersion.ToString();git=(git --version);python=(& $python --version);node=(node --version);npm=(npm --version);uv=(uvx --from uv==0.12.7 uv --version)}
  if($LASTEXITCODE-ne0){Fail-Package 'KG_PACKAGE_LOCK_INVALID'}
  $manifest=[ordered]@{
    schema_version=2;distribution_policy_version='KG_G1_PROJECTION_V1';source_commit=$Commit;source_tree=$tree
    source_object_count=$objects.Count;source_file_count=$objects.Count;migration_head=$migrationHead;openapi_sha256=$actualOpenApiSha256
    uv_lock_sha256=(Get-FileHash -LiteralPath $uvLock -Algorithm SHA256).Hash;frontend_lock_sha256=(Get-FileHash -LiteralPath $frontendLock -Algorithm SHA256).Hash
    tool_versions=$toolVersions;included_files=$rows;excluded_paths=$excluded;files=$rows
  }
  $manifestRelative='本机联调交付/完整联调包源码清单.json'
  $manifest|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $stage $manifestRelative) -Encoding utf8NoBOM
  $finalActualPaths=@(Get-ChildItem -LiteralPath $stage -Recurse -File -Force|ForEach-Object{[IO.Path]::GetRelativePath($stage,$_.FullName).Replace('\','/')}|Sort-Object)
  $finalExpectedPaths=@(@($expectedObjects.path)+$manifestRelative|Sort-Object)
  if(Compare-Object -ReferenceObject $finalExpectedPaths -DifferenceObject $finalActualPaths){Fail-Package 'KG_PACKAGE_MANIFEST_MISMATCH'}
  $zip=Join-Path $out "KG完整联调基础包_$($Commit.Substring(0,12)).zip"
  Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
  $zipReader=[IO.Compression.ZipFile]::OpenRead($zip)
  try{$zipPaths=@($zipReader.Entries|Where-Object{-not$_.FullName.EndsWith('/')}|ForEach-Object{$_.FullName.Replace('\','/')}|Sort-Object)}finally{$zipReader.Dispose()}
  if(Compare-Object -ReferenceObject $finalExpectedPaths -DifferenceObject $zipPaths){Fail-Package 'KG_PACKAGE_MANIFEST_MISMATCH'}
  $hash=(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
  if([IO.Path]::GetFullPath($stage)-ne[IO.Path]::GetFullPath((Join-Path $out "stage-$($Commit.Substring(0,12))"))-or((Get-Item -LiteralPath $stage -Force).Attributes-band[IO.FileAttributes]::ReparsePoint)-ne0-or[IO.File]::ReadAllText($ownershipPath,[Text.Encoding]::ASCII)-ne$ownershipValue){Fail-Package 'KG_PACKAGE_CLEANUP_SCOPE_INVALID'}
  [IO.Directory]::Delete($stage,$true)
  [IO.File]::Delete($ownershipPath)
  [ordered]@{status='BUILT';code='BUILT';source_commit=$Commit;source_tree=$tree;zip=(Split-Path $zip -Leaf);zip_sha256=$hash;openapi=(Split-Path $openApiArtifact -Leaf);openapi_sha256=$actualOpenApiSha256}|ConvertTo-Json -Compress
} catch {
  [ordered]@{status='FAILED';code=if($_.Exception.Message-match'^KG_'){$_.Exception.Message}else{'KG_PACKAGE_BUILD_FAILED'}}|ConvertTo-Json -Compress
  exit 1
}
