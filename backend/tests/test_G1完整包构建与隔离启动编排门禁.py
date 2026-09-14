from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DELIVERY_ROOT = REPOSITORY_ROOT / "本机联调交付"
EXPECTED_ASSETS = {
    "本机联调公共.ps1",
    "前置软件检查.ps1",
    "准备本机联调.ps1",
    "启动本机联调.ps1",
    "检查本机联调状态.ps1",
    "停止本机联调.ps1",
    "重启本机联调.ps1",
    "显式重置本机联调.ps1",
    "本机联调容器.yml",
    "生成并验证完整APP联调包.ps1",
    "本机联调配置变量模板_V1.json",
    "合成业务初始化器接口_V1.json",
    "请先阅读_完整APP联调范围与启动说明.md",
    "故障码与排查说明_V2.md",
}


def test_G1_R01_交付资产缺失必须显式失败() -> None:
    actual = (
        {path.name for path in DELIVERY_ROOT.iterdir() if path.is_file()}
        if DELIVERY_ROOT.is_dir()
        else set()
    )
    assert actual == EXPECTED_ASSETS, "G1_DELIVERY_ASSETS_MISSING"


def _text(name: str) -> str:
    return (DELIVERY_ROOT / name).read_text(encoding="utf-8")


def test_G1_R02_R04_源码锁与Migration失败码闭合() -> None:
    joined = _text("本机联调公共.ps1") + _text("前置软件检查.ps1")
    for code in (
        "KG_PACKAGE_SOURCE_INVALID",
        "KG_PACKAGE_MANIFEST_MISMATCH",
        "KG_PACKAGE_LOCK_INVALID",
        "KG_PACKAGE_MIGRATION_HEAD_INVALID",
    ):
        assert code in joined


def test_G1_R05_工具仅检查且不得安装系统软件() -> None:
    source = _text("前置软件检查.ps1")
    assert all(tool in source for tool in ("pwsh", "docker", "git", "node", "npm", "uvx"))
    assert not re.search(r"winget|choco|Install-Package|Invoke-WebRequest", source, re.I)


def test_G1_R06_禁入项闭合() -> None:
    source = _text("生成并验证完整APP联调包.ps1")
    for value in (".git", ".venv", "node_modules", "dist", "__pycache__", ".env", ".log", ".xml"):
        assert value in source


def test_G1_R07_R08_Manifest不自散列且ZIP哈希外置() -> None:
    source = _text("生成并验证完整APP联调包.ps1")
    assert "$rows=" in source
    assert source.index("$rows=") < source.index("完整联调包源码清单.json")
    assert "zip_sha256" in source
    assert "zip_sha256=" not in source[source.index("$manifest=") : source.index("$manifest|")]


def test_G1_Windows构包必须用支持中文路径的zip归档且检查每步exit() -> None:
    source = _text("生成并验证完整APP联调包.ps1")
    assert "git -C $root archive --format=zip" in source
    assert "Expand-Archive" in source
    assert "KG_PACKAGE_ARCHIVE_FAILED" in source
    assert "tar -xf" not in source


def test_G1_分发投影只允许排除唯一获批历史JUnit() -> None:
    source = _text("生成并验证完整APP联调包.ps1")
    approved = "frontend/验收证据/组织前端原型对齐V1/前端测试结果.xml"
    assert approved in source
    assert "TRACKED_HISTORICAL_JUNIT_EXCLUDED" in source
    assert "distribution_policy_version" in source
    assert "excluded_paths" in source
    assert "included_files" in source
    assert "<testsuites?" in source
    assert ".Extension-in@('.log','.xml')" not in source


def test_G1_包模式Manifest必须拒绝未列入文件与排除项回流() -> None:
    common = _text("本机联调公共.ps1")
    assert "included_files" in common
    assert "excluded_paths" in common
    assert "KG_G1_PROJECTION_V1" in common
    assert "Compare-Object" in common
    assert "runtimeGenerated" in common
    assert "backend/\\.venv/" in common
    assert "frontend/node_modules/" in common
    assert r"\.env" not in common[common.index("$runtimeGenerated") : common.index("$actual=")]


def test_G1_precheck只校验锁与Migration图不创建依赖环境() -> None:
    source = _text("前置软件检查.ps1")
    assert "uv lock --check" in source
    assert "uv run" not in source
    assert "down_revision" in source
    assert "KG_PACKAGE_MIGRATION_HEAD_INVALID" in source


def test_G1_R09_独立包拒绝绝对路径与上跳() -> None:
    source = _text("本机联调公共.ps1")
    assert "(^[A-Za-z]:)|(^/)|(\\.\\.)" in source
    assert "KG_PACKAGE_MANIFEST_MISMATCH" in source


def test_G1_R10_配置模板不含值且随机值在准备阶段生成() -> None:
    template = json.loads(_text("本机联调配置变量模板_V1.json"))
    assert template["contains_values"] is False
    assert all(item["source"] == "generated_after_extraction" for item in template["variables"][:2])
    assert "New-KgRandomHex" in _text("准备本机联调.ps1")


def test_G1_R11_R12_八类Worker必须真实readiness而非只查进程() -> None:
    start = _text("启动本机联调.ps1")
    status = _text("检查本机联调状态.ps1")
    for kind in ("registration", "private_file", "therapist", "member", "slice4", "slice5", "slice6", "slice7"):
        assert kind in start
        assert f"'{kind}'" in status, "KG_LOCAL_REQUIRED_WORKERS_MISSING"
    assert "scripts.check_worker_readiness import probe_worker" in status
    assert "worker_kind=os.environ['KG_G1_PROBE_KIND']" in status
    assert "/health/live" in status and "/health/ready" in status


def test_G1_readiness探针必须恢复已持久化运行环境() -> None:
    status = _text("检查本机联调状态.ps1")
    assert "foreach($entry in $s.runtime.psobject.Properties)" in status
    assert "SetEnvironmentVariable($entry.Name" in status
    assert "$env:KG_ENV='local'" in status
    assert "$env:KG_DEPLOYMENT_PROFILE='local_ephemeral'" in status


def test_G1_Windows探针必须保留独立进程级有界终止() -> None:
    status = _text("检查本机联调状态.ps1")
    assert "probe_worker" in status
    assert "ProcessStartInfo" in status
    assert "WaitForExit" in status
    assert "Kill($true)" in status
    assert "KG_LOCAL_WORKER_PROBE_PROCESS_TIMEOUT_MS" in status


def test_G1_两个Web入口必须做真实HTTP可访问检查() -> None:
    status = _text("检查本机联调状态.ps1")
    assert "$s.platform_port" in status
    assert "$s.institution_port" in status
    assert 'Invoke-WebRequest' in status
    assert 'web_endpoints' in status


def test_G1_私有文件根必须单独校验可写性() -> None:
    status = _text("检查本机联调状态.ps1")
    common = _text("本机联调公共.ps1")
    assert "private_file_root_writable" in status
    assert "Test-KgPrivateStorageWritable $s" in status
    assert "Join-Path $State.state_dir '私有文件'" in common
    assert "FileMode]::CreateNew" in common


def test_G1_readiness失败只能返回闭合组件名不得返回配置值() -> None:
    status = _text("检查本机联调状态.ps1")
    assert "failed_checks" in status
    assert "otherFailures.Name" in status
    assert "runtime=" not in status


def test_G1_R13_stop与reset严格分离() -> None:
    stop = _text("停止本机联调.ps1")
    reset = _text("显式重置本机联调.ps1")
    assert "--volumes" not in stop and "Remove-Item" not in stop
    assert "--volumes" in reset and "RESET-KG-G1-" in reset and "Assert-KgInside" in reset


def test_G1_R14_重启持久性必须真实检查sentinel() -> None:
    source = _text("重启本机联调.ps1")
    assert "sentinel" in source and "KG_LOCAL_PERSISTENCE_FAILED" in source
    assert "shobj_description" in source
    assert ".g1-persistence-sentinel" in source
    assert "persistence='VERIFIED'" in source


def test_G1_Scanner阻断时重启仍必须完成持久性验证() -> None:
    source = _text("重启本机联调.ps1")
    assert "KG_LOCAL_CAPABILITY_BLOCKED" in source
    assert "ConvertFrom-Json" in source
    assert source.index("KG_LOCAL_CAPABILITY_BLOCKED") < source.index("shobj_description")


def test_G1_start冷启动readiness必须有界收敛而非单次三秒判死() -> None:
    source = _text("启动本机联调.ps1")
    assert "$readinessAttempts=3" in source
    assert "for($attempt=1;$attempt-le$readinessAttempts;$attempt++)" in source
    assert "readiness_attempt_limit" in source
    assert "readiness_history" in source
    assert "KG_LOCAL_CAPABILITY_BLOCKED" in source
    assert "KG_LOCAL_READINESS_INCOMPLETE" in source


def test_G1_start失败必须停止已记录任务进程但不执行reset() -> None:
    source = _text("启动本机联调.ps1")
    catch = source[source.rindex("} catch {") :]
    assert "KG_LOCAL_ALREADY_RUNNING" in source
    assert "$startedThisAttempt=$true" in source
    assert "if($startedThisAttempt-and$s-and$s.processes)" in catch
    assert "Stop-KgOwnedProcesses $s.processes" in catch
    assert "$s.processes=@{}" in catch
    assert "Write-KgState $s" in catch
    assert "down --volumes" not in catch
    assert "Remove-Item" not in catch


def test_G1_R15_Scanner不可用不得伪装全READY() -> None:
    status = _text("检查本机联调状态.ps1")
    assert "KG_LOCAL_SCANNER_PROBE_PROCESS_TIMEOUT_MS" in status
    assert "build_clamav_scanner" in status
    assert "asyncio.run(scanner.health())" in status
    assert "scanner_health" in status
    assert "scanner='BLOCKED'" in status
    assert "capability='FOUNDATION_ONLY'" in status


def test_G1_S1Scanner必须任务隔离_digested_只读且仅loopback() -> None:
    compose = _text("本机联调容器.yml")
    assert "clamav/clamav@sha256:f156095071757e3838caa50265d65e36cdf7f934a27aacf851ea6d2fadbe8200" in compose
    assert "${KG_LOCAL_CLAMD_CONTAINER}" in compose
    assert '127.0.0.1:${KG_LOCAL_CLAMD_PORT}:3310' in compose
    assert "clamav-config:/g1-config:ro" in compose
    assert "name: ${KG_LOCAL_CLAMD_CONFIG_VOLUME}" in compose
    assert "--config-file=/g1-config/clamd.conf" in compose
    assert "clamav-signatures:/var/lib/clamav:ro" in compose
    assert "kg.g1.delivery" in compose


def test_G1_prepare必须初始化并持久化S1Scanner运行合同() -> None:
    prepare = _text("准备本机联调.ps1")
    for token in (
        "clamav_container",
        "clamav_port",
        "KG_LOCAL_CLAMD_CONFIG",
        "KG_PRIVATE_FILE_SCANNER_FACTORY",
        "app.modules.private_file.clamav_scanner:build_clamav_scanner",
        "KG_PRIVATE_FILE_CLAMD_HOST",
        "KG_PRIVATE_FILE_CLAMD_PORT",
        "KG_PRIVATE_FILE_SCANNER_ENGINE_VERSION",
        "freshclam",
    ):
        assert token in prepare
    assert "Replace(\"`r`n\",\"`n\").Replace(\"`r\",\"`n\")" in prepare
    assert "clamav_config_volume" in prepare
    assert "clamav_config_sha256" in prepare
    assert "Assert-KgClamdConfigVolume $state" in prepare
    assert "Wait-KgHealthy $state.clamav_container 180" in prepare


def test_G1_prepare失败必须按run_id清理未落盘任务资源() -> None:
    prepare = _text("准备本机联调.ps1")
    assert "kg.g1.delivery" in prepare
    assert "Assert-KgOwnedResources $state" in prepare
    assert "down --volumes" in prepare
    assert "remove-orphans" not in prepare
    assert "Assert-KgInside $state.state_dir $script:状态根" in prepare


def test_G1_start_stop_reset必须恢复并精确清理Scanner() -> None:
    start = _text("启动本机联调.ps1")
    stop = _text("停止本机联调.ps1")
    reset = _text("显式重置本机联调.ps1")
    assert "Wait-KgHealthy $s.clamav_container 180" in start
    assert "Assert-KgClamdConfigVolume $s" in start
    assert "KG_PRIVATE_FILE_SCANNER_FACTORY" in start
    assert "$s.clamav_container" in stop
    assert "$s.clamav_container" in reset
    assert "clamav_config_volume" in reset and "clamav_signature_volume" in reset


def test_G1_Scanner配置卷必须以run_id_label和规范化Hash双重核验() -> None:
    common = _text("本机联调公共.ps1")
    status = _text("检查本机联调状态.ps1")
    assert "function Assert-KgClamdConfigVolume($State)" in common
    assert "kg.g1.delivery" in common
    assert "clamav_config_sha256" in common
    assert "sha256sum" in common
    assert "KG_LOCAL_SCANNER_CONFIGURATION_INVALID" in common
    assert "Assert-KgClamdConfigVolume $s" in status


def test_G1_R16_G2仅为未实现接口占位() -> None:
    contract = json.loads(_text("合成业务初始化器接口_V1.json"))
    assert contract["implementation"] == "NOT_IMPLEMENTED"
    assert contract["stable_code"] == "KG_LOCAL_BUSINESS_FIXTURE_NOT_IMPLEMENTED"
    assert {"credential", "database_url", "pii", "sql", "role_ddl"} <= set(contract["forbidden_inputs"])


def test_G1_R17_reset必须同时核对绝对路径run_id与label() -> None:
    source = _text("显式重置本机联调.ps1")
    assert all(value in source for value in ("Assert-KgInside", "run_id", "Assert-KgOwnedResources"))
    assert "Stop-KgOwnedProcesses $s.processes" in source
    assert source.index("RESET-KG-G1-") < source.index("Stop-KgOwnedProcesses")
    assert source.index("Assert-KgInside") < source.index("Stop-KgOwnedProcesses")
    assert source.index("Assert-KgOwnedResources") < source.index("Stop-KgOwnedProcesses")
    assert source.index("Stop-KgOwnedProcesses") < source.index("down --volumes")


def test_G1_运行边界不修改业务MigrationWorkflow或锁() -> None:
    raw = __import__("subprocess").run(
        ["git", "status", "--porcelain=v1", "-z"], cwd=REPOSITORY_ROOT, capture_output=True, check=True
    ).stdout
    paths = [item[3:].decode("utf-8") for item in raw.split(b"\0") if item]
    approved_exact = {"backend/tests/test_一期认证受限读取边界合同.py"}
    assert all(
        path.startswith(("本机联调交付/", "backend/tests/test_G1")) or path in approved_exact
        for path in paths
    )


def test_G1_RabbitMQ健康检查必须使用镜像既有rabbitmq身份() -> None:
    compose = _text("本机联调容器.yml")
    assert '["CMD", "su-exec", "rabbitmq", "rabbitmq-diagnostics", "-q", "ping"]' in compose


def test_G1_prepare必须执行正式Migration并记录单一Head() -> None:
    source = _text("准备本机联调.ps1")
    assert "command.upgrade(c,'head')" in source
    assert "get_current_head" in source
    assert "migration_head" in source
    assert "KG_LOCAL_MIGRATION_FAILED" in source
    assert "$state.runtime.KG_DATABASE_URL" not in source
    assert "SetEnvironmentVariable('KG_DATABASE_URL',$null,'Process')" in source


def test_G1_reset_compose失败不得删除状态或宣称成功() -> None:
    source = _text("显式重置本机联调.ps1")
    assert "$env:KG_LOCAL_RUN_ID=$s.run_id" in source
    assert "$env:KG_LOCAL_DATABASE_CONTAINER=$s.database_container" in source
    compose = source.index("& docker @args down --volumes")
    guard = source.index("if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_RESET_FAILED'}")
    delete = source.index("Remove-Item -LiteralPath $script:当前状态")
    assert compose < guard < delete


def test_G1_stop必须恢复compose环境且失败不得宣称成功() -> None:
    source = _text("停止本机联调.ps1")
    assert "$env:KG_LOCAL_RUN_ID=$s.run_id" in source
    assert "$env:KG_LOCAL_DATABASE_CONTAINER=$s.database_container" in source
    compose = source.index("& docker @args stop")
    guard = source.index("if($LASTEXITCODE-ne0){Fail-Kg 'KG_LOCAL_STOP_FAILED'}")
    success = source.index("code='STOPPED'")
    assert compose < guard < success


def test_G1_prepare状态必须预先声明worker_hosts闭合字段() -> None:
    prepare = _text("准备本机联调.ps1")
    assert "worker_hosts=@{}" in prepare


def test_G1_prepare必须生成认证校验要求的闭合密钥用途() -> None:
    prepare = _text("准备本机联调.ps1")
    assert "SIGNING_KEY" in prepare, "G1_AUTH_SIGNING_KEY_DISCOVERY_MISSING"
    assert "KEK_B64" in prepare, "G1_AUTH_KEK_DISCOVERY_MISSING"


def test_G1_prepare必须对齐Registration已有disposable边界() -> None:
    prepare = _text("准备本机联调.ps1")
    assert '$databaseName="kg_it_$run"' in prepare
    assert "KG_TEST_ENVIRONMENT='local_disposable'" in prepare
    assert "KG_TEST_RUN_ID=$run" in prepare
    assert "kg-test-disposable:$run" in prepare
    assert "KG_TEST_APPLICATION_ROLE" in prepare
    assert "KG_TEST_DELIVERY_WORKER_ROLE" in prepare


def test_G1_start必须显式命名正式LocalFilesystem配置() -> None:
    start = _text("启动本机联调.ps1")
    status = _text("检查本机联调状态.ps1")
    assert "$env:KG_FILE_STORAGE_BACKEND='local_filesystem'" in start
    assert "$env:KG_FILE_STORAGE_BACKEND='local_filesystem'" in status
    assert "KG_PRIVATE_FILE_STORAGE_ROOT" in start


def test_G1_状态写入必须接受JSON反序列化对象() -> None:
    common = _text("本机联调公共.ps1")
    assert "function Write-KgState($State)" in common
    assert "function Write-KgState([hashtable]" not in common


def test_G1_状态ACL只授当前用户但必须允许显式reset删除() -> None:
    common = _text("本机联调公共.ps1")
    assert '"${env:USERNAME}:(M)"' in common
    assert '"${env:USERNAME}:(F)"' not in common
    assert '"${env:USERNAME}:(R,W)"' not in common
    assert common.index("Initialize-KgPrivateFile $tmp") < common.index("Move-Item -LiteralPath $tmp")


def test_G1_I1_cleanup必须对3容器4卷和网络逐项确权且禁止remove_orphans() -> None:
    prepare = _text("准备本机联调.ps1")
    reset = _text("显式重置本机联调.ps1")
    compose = _text("本机联调容器.yml")
    common = _text("本机联调公共.ps1")
    for field in (
        "database_container",
        "rabbitmq_container",
        "clamav_container",
        "database_volume",
        "rabbitmq_volume",
        "clamav_config_volume",
        "clamav_signature_volume",
        "network_name",
    ):
        assert field in prepare
    assert "function Assert-KgOwnedResources" in common
    assert "KG_LOCAL_RESOURCE_OWNERSHIP_UNKNOWN" in common
    assert "Assert-KgOwnedResources $s" in reset
    assert "Assert-KgOwnedResources $state" in prepare
    assert "remove-orphans" not in prepare + reset
    assert "KG_LOCAL_DATABASE_VOLUME" in compose
    assert "KG_LOCAL_RABBITMQ_VOLUME" in compose
    assert "KG_LOCAL_NETWORK" in compose


def test_G1_I2_process清理必须绑定PID创建时间与任务标识() -> None:
    common = _text("本机联调公共.ps1")
    status = _text("检查本机联调状态.ps1")
    joined = common + _text("停止本机联调.ps1") + _text("显式重置本机联调.ps1")
    for token in ("created_at_utc_ticks", "identity_marker", "Test-KgProcessIdentity", "Stop-KgOwnedProcess", "Stop-KgOwnedProcesses"):
        assert token in joined
    assert "Win32_Process" in common
    assert "taskkill.exe" in common
    assert "Stop-KgProcessTree" not in joined
    assert "Test-KgProcessIdentity $p.Value" in status


def test_G1_I3_secret目录与临时文件必须先限权再写入() -> None:
    common = _text("本机联调公共.ps1")
    prepare = _text("准备本机联调.ps1")
    assert "function Initialize-KgPrivateDirectory" in common
    assert "function Initialize-KgPrivateFile" in common
    assert common.index("Initialize-KgPrivateFile $tmp") < common.index("ConvertTo-Json -Depth 8")
    assert prepare.index("Initialize-KgPrivateDirectory $stateDir") < prepare.index("New-KgRandomHex 24")
    assert prepare.index("Initialize-KgPrivateFile $sqlPath") < prepare.index("$sql|Set-Content")
    assert "finally{[IO.File]::Delete($sqlPath)}" in prepare


def test_G1_I4_package必须绑定Git对象集并拒绝覆盖已有输出() -> None:
    source = _text("生成并验证完整APP联调包.ps1")
    common = _text("本机联调公共.ps1")
    for token in (
        "git -C $root ls-tree -r -z --full-tree",
        "migration_head",
        "openapi_sha256",
        "uv_lock_sha256",
        "frontend_lock_sha256",
        "tool_versions",
        "source_object_count",
        "KG_PACKAGE_OUTPUT_ALREADY_EXISTS",
        "KG_PACKAGE_UNSUPPORTED_GIT_OBJECT",
        "KG_PACKAGE_PATH_COLLISION",
    ):
        assert token in source
    assert "Remove-Item -LiteralPath $stage -Recurse -Force" not in source
    assert "status --porcelain=v1" in common


def _run_pwsh(body: str) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_G1_I1_cleanup确权必须区分不存在与查询未知并拒绝标签不匹配() -> None:
    common = str(DELIVERY_ROOT / "本机联调公共.ps1").replace("'", "''")
    script = rf"""
. '{common}'
function global:Assert-KgStateIdentity {{}}
$state=[pscustomobject]@{{run_id='0123456789abcdef';database_container='kg-g1-db-0123456789abcdef';rabbitmq_container='kg-g1-rabbit-0123456789abcdef';clamav_container='kg-g1-clamd-0123456789abcdef';database_volume='kg-g1-db-data-0123456789abcdef';rabbitmq_volume='kg-g1-rabbit-data-0123456789abcdef';clamav_config_volume='kg-g1-clamd-config-0123456789abcdef';clamav_signature_volume='kg-g1-clamd-signatures-0123456789abcdef';network_name='kg-g1-net-0123456789abcdef'}}
function global:docker {{
  $joined=$args -join ' '
  if($env:G1_FAKE_DOCKER_CASE -eq 'list-failure'){{$global:LASTEXITCODE=1;return}}
  if($env:G1_FAKE_DOCKER_CASE -eq 'absent'){{$global:LASTEXITCODE=0;return}}
  if($joined -match 'container ls'){{$global:LASTEXITCODE=0;'kg-g1-db-0123456789abcdef';return}}
  if($env:G1_FAKE_DOCKER_CASE -eq 'inspect-failure'){{$global:LASTEXITCODE=1;return}}
  $global:LASTEXITCODE=0
  if($env:G1_FAKE_DOCKER_CASE -eq 'label-mismatch'){{'foreign'}}else{{'0123456789abcdef'}}
}}
try{{Assert-KgOwnedResources $state;'OK'}}catch{{$_.Exception.Message}}
"""
    expected = {
        "absent": "OK",
        "list-failure": "KG_LOCAL_RESOURCE_OWNERSHIP_UNKNOWN",
        "inspect-failure": "KG_LOCAL_RESOURCE_OWNERSHIP_UNKNOWN",
        "label-mismatch": "KG_LOCAL_CLEANUP_SCOPE_INVALID",
    }
    for case, code in expected.items():
        completed = _run_pwsh(f"$env:G1_FAKE_DOCKER_CASE='{case}'\n{script}")
        assert completed.returncode == 0
        assert completed.stdout.strip().splitlines()[-1] == code


def test_G1_I2_process清理不得误杀PID复用且停止失败可见() -> None:
    common = str(DELIVERY_ROOT / "本机联调公共.ps1").replace("'", "''")
    script = rf"""
. '{common}'
$global:kills=0
function global:Get-Process {{param($Id,$ErrorAction);if($env:G1_PROCESS_CASE-ne'absent'){{[pscustomobject]@{{Id=$Id;StartTime=[datetime]::new(638000000000000000,[DateTimeKind]::Utc)}}}}}}
function global:Get-CimInstance {{param($ClassName,$Filter,$ErrorAction);$marker=if($env:G1_PROCESS_CASE-eq'mismatch'){{'foreign'}}else{{'g1-owned'}};[pscustomobject]@{{CommandLine="pwsh $marker"}}}}
function global:Invoke-KgTaskkill {{param($ProcessId);$global:kills++;if($env:G1_PROCESS_CASE-eq'kill-failure'){{1}}else{{0}}}}
$record=[pscustomobject]@{{pid=42;created_at_utc_ticks=638000000000000000;identity_marker='g1-owned'}}
try{{Stop-KgOwnedProcess $record;"OK:$global:kills"}}catch{{"$($_.Exception.Message):$global:kills"}}
"""
    expected = {
        "absent": "OK:0",
        "mismatch": "KG_LOCAL_PROCESS_OWNERSHIP_INVALID:0",
        "owned": "OK:1",
        "kill-failure": "KG_LOCAL_PROCESS_STOP_FAILED:1",
    }
    for case, code in expected.items():
        completed = _run_pwsh(f"$env:G1_PROCESS_CASE='{case}'\n{script}")
        assert completed.returncode == 0
        assert completed.stdout.strip().splitlines()[-1] == code


def test_G1_管理员Migration连接不得持久化且未知角色映射必须拒绝() -> None:
    prepare = _text("准备本机联调.ps1")
    assert "$state.runtime.KG_DATABASE_URL" not in prepare
    assert "SetEnvironmentVariable('KG_DATABASE_URL',$null,'Process')" in prepare
    assert "KG_LOCAL_ROLE_MAPPING_UNKNOWN" in prepare
    assert "$role=$roleMap['KG_DATABASE_USER']" not in prepare


def test_G1_state必须把schema_run目录与compose项目精确绑定(tmp_path: Path) -> None:
    common = str(DELIVERY_ROOT / "本机联调公共.ps1").replace("'", "''")
    root = str(tmp_path).replace("'", "''")
    script = rf"""
. '{common}'
$script:状态根='{root}'
function global:Assert-KgNoReparsePath {{}}
function global:Assert-KgPrivateAcl {{}}
$run='0123456789abcdef';New-Item -ItemType Directory -Path (Join-Path $script:状态根 $run)|Out-Null
function Check($schema,$project,$dir){{$s=[pscustomobject]@{{schema_version=$schema;run_id=$run;compose_project=$project;state_dir=$dir}};try{{Assert-KgStateIdentity $s;'OK'}}catch{{$_.Exception.Message}}}}
Check 2 "kg-g1-$run" (Join-Path $script:状态根 $run)
Check 1 "kg-g1-$run" (Join-Path $script:状态根 $run)
Check 2 'foreign-project' (Join-Path $script:状态根 $run)
Check 2 "kg-g1-$run" (Join-Path $script:状态根 'fedcba9876543210')
"""
    completed = _run_pwsh(script)
    assert completed.returncode == 0
    assert completed.stdout.strip().splitlines() == [
        "OK",
        "KG_LOCAL_CLEANUP_SCOPE_INVALID",
        "KG_LOCAL_CLEANUP_SCOPE_INVALID",
        "KG_LOCAL_CLEANUP_SCOPE_INVALID",
    ]


def test_G1_private_root缺失或reparse必须拒绝且探针不残留(tmp_path: Path) -> None:
    common = str(DELIVERY_ROOT / "本机联调公共.ps1").replace("'", "''")
    root = str(tmp_path).replace("'", "''")
    script = rf"""
. '{common}'
function global:Assert-KgStateIdentity {{}}
$state=[pscustomobject]@{{run_id='0123456789abcdef';state_dir='{root}'}}
"missing=$(Test-KgPrivateStorageWritable $state)"
$private=Join-Path $state.state_dir '私有文件';New-Item -ItemType Directory -Path $private|Out-Null
"real=$(Test-KgPrivateStorageWritable $state)"
"residue=$(@(Get-ChildItem -LiteralPath $private -Force).Count)"
[IO.Directory]::Delete($private,$true)
$target=Join-Path $state.state_dir 'target';New-Item -ItemType Directory -Path $target|Out-Null
if($IsWindows){{New-Item -ItemType Junction -Path $private -Target $target|Out-Null}}else{{New-Item -ItemType SymbolicLink -Path $private -Target $target|Out-Null}}
"reparse=$(Test-KgPrivateStorageWritable $state)"
"""
    completed = _run_pwsh(script)
    assert completed.returncode == 0
    assert completed.stdout.strip().splitlines() == [
        "missing=False",
        "real=True",
        "residue=0",
        "reparse=False",
    ]


def test_G1_I4_manifest必须重算GitBlob并从exact_tree生成OpenAPI() -> None:
    package = _text("生成并验证完整APP联调包.ps1")
    precheck = _text("前置软件检查.ps1")
    for token in (
        "hash-object --no-filters",
        "$actualOid-ne$_.oid",
        "KG_PACKAGE_OPENAPI_INVALID",
        "from app.main import create_app",
        "create_app().openapi()",
        "KG_G1_OPENAPI_OUTPUT",
        "StartsWith('KG_'",
        "python=(& $python --version)",
    ):
        assert token in package
    assert "$actualOpenApiSha256-ne$OpenApiSha256.ToUpperInvariant()" in package
    assert ".kg-g1-package-owner" in package
    assert "FileMode]::CreateNew" in package
    assert "ReadAllText($ownershipPath" in package
    assert "FileAttributes]::ReparsePoint" in package
    assert "$source.migration_head-ne$heads[0]" in precheck


def test_G1_read_state必须在解析JSON前复核ACL路径和身份绑定() -> None:
    common = _text("本机联调公共.ps1")
    read = common[common.index("function Read-KgState") : common.index("function Write-KgState")]
    assert read.index("Assert-KgNoReparsePath") < read.index("ConvertFrom-Json")
    assert read.index("Assert-KgPrivateAcl $script:当前状态") < read.index("ConvertFrom-Json")
    assert read.index("ConvertFrom-Json") < read.index("Assert-KgStateIdentity $state")
    assert "schema_version-ne2" in common
    assert 'compose_project-ne"kg-g1-$run"' in common
    assert "Join-Path $script:状态根 $run" in common


def test_G1_reparse_guard必须同时支持普通文件和目录(tmp_path: Path) -> None:
    common = str(DELIVERY_ROOT / "本机联调公共.ps1").replace("'", "''")
    root = str(tmp_path).replace("'", "''")
    script = rf"""
. '{common}'
$directory=Join-Path '{root}' 'ordinary';New-Item -ItemType Directory -Path $directory|Out-Null
$file=Join-Path $directory 'state.json';[IO.File]::WriteAllText($file,'{{}}')
try{{Assert-KgNoReparsePath $directory;'DIRECTORY_OK'}}catch{{$_.Exception.Message}}
try{{Assert-KgNoReparsePath $file;'FILE_OK'}}catch{{$_.Exception.Message}}
"""
    completed = _run_pwsh(script)
    assert completed.returncode == 0
    assert completed.stdout.strip().splitlines() == ["DIRECTORY_OK", "FILE_OK"]


def test_G1_OpenAPI生成禁止字节码且压缩前后文件集合必须精确() -> None:
    package = _text("生成并验证完整APP联调包.ps1")
    assert "PYTHONDONTWRITEBYTECODE" in package
    assert "ArgumentList.Add('-B')" in package
    assert "$finalActualPaths" in package
    assert "$finalExpectedPaths" in package
    assert "ZipFile]::OpenRead" in package
    assert "KG_PACKAGE_MANIFEST_MISMATCH" in package
