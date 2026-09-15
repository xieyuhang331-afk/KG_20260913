. (Join-Path $PSScriptRoot '本机联调公共.ps1')
try {
  $source=Get-KgSource
  foreach($cmd in @('pwsh','docker','git','node','npm','uvx')){if(-not(Get-Command $cmd -ErrorAction SilentlyContinue)){Fail-Kg 'KG_LOCAL_PREREQUISITE_FAILED'}}
  docker info *> $null; if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_PREREQUISITE_FAILED'}
  foreach($p in @('backend/pyproject.toml','backend/uv.lock','frontend/package.json','frontend/package-lock.json','backend/alembic.ini')){if(-not(Test-Path -LiteralPath (Join-Path $script:仓库根 $p))){Fail-Kg 'KG_PACKAGE_LOCK_INVALID'}}
  Push-Location (Join-Path $script:仓库根 'backend');try{& uvx --from uv==0.12.7 uv lock --check *> $null;if($LASTEXITCODE-ne0){Fail-Kg 'KG_PACKAGE_LOCK_INVALID'}}finally{Pop-Location}
  $revisions=@{};$referenced=[Collections.Generic.HashSet[string]]::new()
  foreach($file in Get-ChildItem -LiteralPath (Join-Path $script:仓库根 'backend/app/migrations/versions') -Filter '*.py'){
    $text=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
    $revisionMatch=[regex]::Match($text,'(?m)^revision\s*=\s*["'']([^"'']+)["'']')
    $downMatch=[regex]::Match($text,'(?m)^down_revision\s*=\s*(?:None|["'']([^"'']+)["''])')
    if(-not$revisionMatch.Success-or-not$downMatch.Success-or$revisions.ContainsKey($revisionMatch.Groups[1].Value)){Fail-Kg 'KG_PACKAGE_MIGRATION_HEAD_INVALID'}
    $revisions[$revisionMatch.Groups[1].Value]=$true
    if($downMatch.Groups[1].Success){[void]$referenced.Add($downMatch.Groups[1].Value)}
  }
  $heads=@($revisions.Keys|Where-Object{-not$referenced.Contains($_)})
  if($heads.Count-ne1){Fail-Kg 'KG_PACKAGE_MIGRATION_HEAD_INVALID'}
  if($source.mode-eq'package'-and$source.migration_head-ne$heads[0]){Fail-Kg 'KG_PACKAGE_MIGRATION_HEAD_INVALID'}
  @{status='READY';code='READY'}|ConvertTo-Json -Compress
} catch { @{status='NOT_READY';code=if($_.Exception.Message -match '^KG_'){ $_.Exception.Message }else{'KG_LOCAL_PREREQUISITE_FAILED'}}|ConvertTo-Json -Compress; exit 1 }
