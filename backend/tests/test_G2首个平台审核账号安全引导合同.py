from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
PYTHON_RUNNER = BACKEND_ROOT / "scripts" / "初始化G2首个平台审核账号.py"
POWERSHELL_WRAPPER = REPOSITORY_ROOT / "本机联调交付" / "初始化G2首个平台审核账号.ps1"


def _load_runner():
    spec = importlib.util.spec_from_file_location("g2_bootstrap_runner", PYTHON_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_G2_B_R01_四文件入口与生产脚本必须精确存在():
    assert PYTHON_RUNNER.is_file(), "G2_B_PYTHON_RUNNER_MISSING"
    assert POWERSHELL_WRAPPER.is_file(), "G2_B_POWERSHELL_WRAPPER_MISSING"


def test_G2_B_R02_CLI没有敏感参数或任意载荷入口():
    tree = ast.parse(PYTHON_RUNNER.read_text(encoding="utf-8"))
    text = PYTHON_RUNNER.read_text(encoding="utf-8")
    forbidden = (
        "--database-url",
        "--password",
        "--phone",
        "--user-id",
        "--sql",
        "--json",
        "parse_known_args",
    )
    assert all(value not in text for value in forbidden)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    add_argument_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"
    ]
    assert add_argument_calls == []


def test_G2_B_R02_未知参数只返回稳定匿名错误(monkeypatch, capsys):
    module = _load_runner()
    secret_value = "synthetic-credentialed-value"
    monkeypatch.setattr(module.sys, "argv", ["runner", "--unknown", secret_value])
    assert module.main() == 2
    output = capsys.readouterr()
    assert output.err == ""
    assert secret_value not in output.out
    assert "--unknown" not in output.out
    assert "KG_G2_BOOTSTRAP_ARGUMENT_INVALID" in output.out


def test_G2_B_R06_R07_请求对象repr不暴露凭据且复用正式密码散列():
    module = _load_runner()
    request = module.BootstrapRequest(
        schema_version=1,
        run_id="a" * 16,
        phone="13900000000",
        password="synthetic-secret",
        password_hash="synthetic-hash",
        request_digest="b" * 64,
    )
    representation = repr(request)
    assert "13900000000" not in representation
    assert "synthetic-secret" not in representation
    assert "synthetic-hash" not in representation
    source = inspect.getsource(module)
    assert "from app.modules.auth.service import hash_password" in source
    assert "jwt" not in source.lower()


def test_G2_B_R03_运行域repr不暴露数据库run或sentinel():
    module = _load_runner()
    scope = module.RuntimeScope.synthetic_valid()
    representation = repr(scope)
    assert scope.database_name not in representation
    assert scope.run_id not in representation
    assert scope.sentinel not in representation


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "production"),
        ("database_host", "10.0.0.8"),
        ("database_name", "shared_database"),
        ("run_id", "../unsafe"),
        ("sentinel", "wrong"),
        ("migration_head", "20260914_0045"),
    ],
    ids=("environment", "host", "database", "run-id", "sentinel", "head"),
)
def test_G2_B_R03_R04_运行域漂移稳定拒绝(field: str, value: str):
    module = _load_runner()
    scope = module.RuntimeScope.synthetic_valid()
    invalid = scope.with_value(field, value)
    with pytest.raises(module.BootstrapError) as caught:
        module.validate_runtime_scope(invalid)
    assert caught.value.code == "KG_G2_BOOTSTRAP_SCOPE_INVALID"
    assert value not in str(caught.value)


def test_G2_B_R05_R05A_Fresh清单必须全部为零():
    module = _load_runner()
    assert module.validate_fresh_manifest({name: 0 for name in module.FRESH_TABLES}) is None
    manifest = {name: 0 for name in module.FRESH_TABLES}
    manifest[next(iter(module.FRESH_TABLES))] = 1
    with pytest.raises(module.BootstrapError) as caught:
        module.validate_fresh_manifest(manifest)
    assert caught.value.code == "KG_G2_BOOTSTRAP_DATABASE_NOT_FRESH"
    assert "user" not in str(caught.value).lower()


def test_G2_B_R05A_提交后清单只允许一行账号():
    module = _load_runner()
    manifest = {name: 0 for name in module.FRESH_TABLES}
    manifest['public."user"'] = 1
    assert module.validate_committed_manifest(manifest) is None
    manifest["public.tenant"] = 1
    with pytest.raises(module.BootstrapError) as caught:
        module.validate_committed_manifest(manifest)
    assert caught.value.code == "KG_G2_BOOTSTRAP_POSTIMAGE_INVALID"


def test_G2_B_R05_首次数据库Fresh预检必须早于随机凭据落盘():
    module = _load_runner()
    source = inspect.getsource(module.run_bootstrap)
    assert source.index("await _require_initial_fresh(engine)") < source.index(
        "_load_or_prepare_request(scope)"
    )


def test_G2_B_R05B_仅插入连接使用READ_COMMITTED且确认连接保持SERIALIZABLE():
    module = _load_runner()
    run_source = inspect.getsource(module.run_bootstrap)
    insert_source = inspect.getsource(module._insert_once)
    confirm_source = inspect.getsource(module._confirm)
    assert 'isolation_level="SERIALIZABLE"' in run_source
    assert 'execution_options(isolation_level="READ COMMITTED")' in insert_source
    assert insert_source.index('execution_options(isolation_level="READ COMMITTED")') < (
        insert_source.index("connection.begin()")
    )
    assert "execution_options" not in confirm_source


@pytest.mark.asyncio
async def test_G2_B_R05B_插入专用隔离先于事务且锁覆盖Fresh判断(monkeypatch):
    module = _load_runner()
    events: list[str] = []

    class Transaction:
        is_active = True

        async def rollback(self):
            events.append("rollback")

    class Connection:
        async def execution_options(self, **options):
            events.append(f"isolation:{options.get('isolation_level')}")
            return self

        async def begin(self):
            events.append("begin")
            return Transaction()

        async def execute(self, statement, *_args, **_kwargs):
            assert "pg_advisory_xact_lock" in str(statement)
            events.append("advisory-lock")

        async def close(self):
            events.append("close")

    class Engine:
        async def connect(self):
            events.append("connect")
            return Connection()

    scope = module.RuntimeScope.synthetic_valid()
    request = module.BootstrapRequest(
        schema_version=1,
        run_id=scope.run_id,
        phone="13900000000",
        password="synthetic-secret-value",
        password_hash="synthetic-password-hash",
        request_digest="b" * 64,
    )

    async def fetch_scope(_):
        events.append("scope")
        return scope

    async def nonfresh(_):
        events.append("manifest")
        return {name: 1 for name in module.FRESH_TABLES}

    monkeypatch.setattr(module, "_fetch_scope", fetch_scope)
    monkeypatch.setattr(module, "_manifest", nonfresh)
    with pytest.raises(module.BootstrapError) as caught:
        await module._insert_once(Engine(), request)
    assert caught.value.code == "KG_G2_BOOTSTRAP_DATABASE_NOT_FRESH"
    assert events == [
        "connect",
        "isolation:READ COMMITTED",
        "begin",
        "scope",
        "advisory-lock",
        "manifest",
        "rollback",
        "close",
    ]


@pytest.mark.asyncio
async def test_G2_B_R05B_锁域只接受当前数据库已验证run_id(monkeypatch):
    module = _load_runner()
    scope = module.RuntimeScope.synthetic_valid()
    events: list[str] = []

    class Transaction:
        is_active = True

        async def rollback(self):
            events.append("rollback")

    class Connection:
        async def execution_options(self, **_options):
            return self

        async def begin(self):
            return Transaction()

        async def execute(self, *_args, **_kwargs):
            events.append("advisory-lock")

        async def close(self):
            events.append("close")

    class Engine:
        async def connect(self):
            return Connection()

    async def fetch_scope(_):
        return scope

    monkeypatch.setattr(module, "_fetch_scope", fetch_scope)
    request = module.BootstrapRequest(
        schema_version=1,
        run_id="b" * 16,
        phone="13900000000",
        password="synthetic-secret-value",
        password_hash="synthetic-password-hash",
        request_digest="c" * 64,
    )
    with pytest.raises(module.BootstrapError) as caught:
        await module._insert_once(Engine(), request)
    assert caught.value.code == "KG_G2_BOOTSTRAP_SCOPE_INVALID"
    assert events == ["rollback", "close"]


def test_G2_B_R09_R11_确认结果仅有闭合三态且UNKNOWN不可重放():
    module = _load_runner()
    assert set(module.CommitOutcome) == {
        module.CommitOutcome.COMMITTED,
        module.CommitOutcome.NOT_COMMITTED,
        module.CommitOutcome.UNKNOWN,
    }
    assert module.CommitOutcome.UNKNOWN.retryable is False
    assert module.CommitOutcome.NOT_COMMITTED.retryable is True


def test_G2_B_R14_模块不得含DDL迁移授权或任意SQL入口():
    text = PYTHON_RUNNER.read_text(encoding="utf-8")
    lowered = text.lower()
    for forbidden in (
        "create role",
        "create function",
        "create table",
        "create trigger",
        "grant ",
        "revoke ",
        "command.upgrade",
        "command.downgrade",
    ):
        assert forbidden not in lowered
    tree = ast.parse(text)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "alembic" not in imports
    assert 'INSERT INTO public."user"' in text
    assert "RETURNING id" in text


def test_G2_B_R13A_Windows包装器先互斥再生成并在finally清除owner环境():
    text = POWERSHELL_WRAPPER.read_text(encoding="utf-8")
    assert ". (Join-Path $PSScriptRoot '本机联调公共.ps1')" in text
    assert "System.Threading.Mutex" in text
    assert text.index("System.Threading.Mutex") < text.index("KG_G2_BOOTSTRAP_PREPARED_PATH")
    assert "Initialize-KgPrivateFile" in text
    assert "Assert-KgPrivateAcl" in text
    assert "KG_G2_BOOTSTRAP_OWNER_DATABASE_URL" in text
    assert "finally" in text
    assert "[Environment]::SetEnvironmentVariable('KG_G2_BOOTSTRAP_OWNER_DATABASE_URL',$null,'Process')" in text


def test_G2_B_R09A_PREPARED先落盘且同run重放完全相同(tmp_path, monkeypatch):
    module = _load_runner()
    scope = module.RuntimeScope.synthetic_valid()
    target = tmp_path / "credential.json"
    temporary = tmp_path / "credential.tmp"
    temporary.touch()
    monkeypatch.setenv("KG_G2_BOOTSTRAP_PREPARED_PATH", str(target))
    monkeypatch.setenv("KG_G2_BOOTSTRAP_PREPARED_TEMP_PATH", str(temporary))
    request = module.BootstrapRequest(
        schema_version=1,
        run_id=scope.run_id,
        phone="13900000000",
        password="synthetic-secret-value",
        password_hash="synthetic-password-hash",
        request_digest="0" * 64,
    )
    request = module.replace(request, request_digest=module._request_digest(request))
    monkeypatch.setattr(module, "_new_request", lambda _: request)
    first, replay = module._load_or_prepare_request(scope)
    second, second_replay = module._load_or_prepare_request(scope)
    assert first == second == request
    assert replay is False and second_replay is True
    assert target.is_file() and not temporary.exists()


def test_G2_B_R09_数据库确认后可安全补写缺失本地回执(tmp_path, monkeypatch):
    module = _load_runner()
    target = tmp_path / "receipt.json"
    temporary = tmp_path / "receipt.tmp"
    temporary.touch()
    monkeypatch.setenv("KG_G2_BOOTSTRAP_RECEIPT_PATH", str(target))
    monkeypatch.setenv("KG_G2_BOOTSTRAP_RECEIPT_TEMP_PATH", str(temporary))
    request = module.BootstrapRequest(
        schema_version=1,
        run_id="a" * 16,
        phone="13900000000",
        password="synthetic-secret-value",
        password_hash="synthetic-password-hash",
        request_digest="b" * 64,
    )
    digest = module._write_receipt(request)
    assert target.is_file() and not temporary.exists()
    assert digest == module.hashlib.sha256(target.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_G2_B_R12_取消不被rollback与close失败覆盖(monkeypatch):
    module = _load_runner()
    events: list[str] = []

    class Transaction:
        is_active = True

        async def rollback(self):
            events.append("rollback")
            raise RuntimeError("cleanup")

    class Connection:
        async def execution_options(self, **_options):
            return self

        async def begin(self):
            return Transaction()

        async def close(self):
            events.append("close")
            raise RuntimeError("cleanup")

    class Engine:
        async def connect(self):
            return Connection()

    async def cancelled(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(module, "_fetch_scope", cancelled)
    request = module.BootstrapRequest(
        schema_version=1,
        run_id="a" * 16,
        phone="13900000000",
        password="synthetic-secret-value",
        password_hash="synthetic-password-hash",
        request_digest="b" * 64,
    )
    with pytest.raises(asyncio.CancelledError):
        await module._insert_once(Engine(), request)
    assert events == ["rollback", "close"]


@pytest.mark.asyncio
async def test_G2_B_R12_commit取消仍尽力rollback和close(monkeypatch):
    module = _load_runner()
    events: list[str] = []

    class Result:
        def scalar_one(self):
            return 1

    class Transaction:
        is_active = True

        async def commit(self):
            events.append("commit")
            raise asyncio.CancelledError

        async def rollback(self):
            events.append("rollback")
            raise RuntimeError("cleanup")

    class Connection:
        async def execution_options(self, **_options):
            return self

        async def begin(self):
            return Transaction()

        async def execute(self, *_args, **_kwargs):
            return Result()

        async def close(self):
            events.append("close")
            raise RuntimeError("cleanup")

    class Engine:
        async def connect(self):
            return Connection()

    request = module.BootstrapRequest(
        schema_version=1,
        run_id="a" * 16,
        phone="13900000000",
        password="synthetic-secret-value",
        password_hash="synthetic-password-hash",
        request_digest="b" * 64,
    )

    async def valid_scope(_):
        return module.RuntimeScope.synthetic_valid()

    async def empty_manifest(_):
        return {name: 0 for name in module.FRESH_TABLES}

    async def rows(_):
        return [
            {
                "phone": request.phone,
                "password_hash": request.password_hash,
                "role": "super_admin",
                "status": "active",
                "tenant_id": None,
                "verify_status": None,
                "real_name": None,
                "id_card": None,
                "exited_at": None,
                "deletion_requested_at": None,
                "user_status": "customer",
            }
        ]

    call_count = 0

    async def manifests(_):
        nonlocal call_count
        call_count += 1
        value = await empty_manifest(None)
        if call_count == 2:
            value['public."user"'] = 1
        return value

    monkeypatch.setattr(module, "_fetch_scope", valid_scope)
    monkeypatch.setattr(module, "_manifest", manifests)
    monkeypatch.setattr(module, "_matching_rows", rows)
    with pytest.raises(asyncio.CancelledError):
        await module._insert_once(Engine(), request)
    assert events == ["commit", "rollback", "close"]


def test_G2_B_R16_错误输出Schema闭合且不含内部字段():
    module = _load_runner()
    payload = module.safe_error_payload(module.BootstrapError("KG_G2_BOOTSTRAP_POSTIMAGE_INVALID"))
    assert payload == {"status": "FAILED", "code": "KG_G2_BOOTSTRAP_POSTIMAGE_INVALID"}
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in ("phone", "password", "user_id", "database", "sql", "url"):
        assert forbidden not in serialized.lower()
