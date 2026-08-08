import asyncio
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import quote

import pytest


def test_CeleryWorkerRuntimeBootstrap尚未实现():
    try:
        from app.tasks.registration_outbox_worker_bootstrap import (
            RegistrationOutboxWorkerBootstrap,
        )
    except (ImportError, ModuleNotFoundError):
        raise AssertionError(
            "Registration Celery worker bootstrap is not implemented"
        ) from None

    assert RegistrationOutboxWorkerBootstrap is not None


def test_Bootstrap创建两套隔离Engine并在同一事件循环Dispose():
    asyncio.run(_assert_engine_lifecycle())


async def _assert_engine_lifecycle():
    from app.tasks.registration_outbox_worker_bootstrap import (
        RegistrationOutboxWorkerBootstrap,
    )

    events = []

    class Engine:
        def __init__(self, role):
            self.role = role

        async def dispose(self):
            events.append(("dispose", self.role, id(asyncio.get_running_loop())))

    def engine_factory(url, **kwargs):
        role = "identity" if url == "identity-secret" else "worker"
        events.append(("engine", role, id(asyncio.get_running_loop())))
        assert kwargs == {"pool_pre_ping": True}
        return Engine(role)

    def session_factory_builder(*, bind, **kwargs):
        events.append(("session_factory", bind.role, id(asyncio.get_running_loop())))
        assert kwargs == {"expire_on_commit": False, "autoflush": False}
        return f"{bind.role}-session-factory"

    class Composition:
        def __init__(self, **kwargs):
            events.append(
                (
                    "composition",
                    kwargs["identity_session_factory"],
                    kwargs["worker_session_factory"],
                )
            )

    class Runtime:
        def __init__(self, composition):
            self.composition = composition

    bootstrap = RegistrationOutboxWorkerBootstrap(
        identity_database_url="identity-secret",
        worker_database_url="worker-secret",
        engine_factory=engine_factory,
        session_factory_builder=session_factory_builder,
        composition_factory=Composition,
        runtime_factory=Runtime,
        clock=lambda: datetime.now(timezone.utc),
        uuid_generator=object(),
    )
    loop_id = id(asyncio.get_running_loop())
    async with bootstrap as runtime:
        assert isinstance(runtime, Runtime)

    assert events == [
        ("engine", "identity", loop_id),
        ("engine", "worker", loop_id),
        ("session_factory", "identity", loop_id),
        ("session_factory", "worker", loop_id),
        ("composition", "identity-session-factory", "worker-session-factory"),
        ("dispose", "worker", loop_id),
        ("dispose", "identity", loop_id),
    ]


def test_Bootstrap异常路径仍Dispose两套Engine():
    asyncio.run(_assert_exception_cleanup())


async def _assert_exception_cleanup():
    from app.tasks.registration_outbox_worker_bootstrap import (
        RegistrationOutboxWorkerBootstrap,
    )

    disposed = []

    class Engine:
        def __init__(self, name):
            self.name = name

        async def dispose(self):
            disposed.append(self.name)

    engines = iter((Engine("identity"), Engine("worker")))
    bootstrap = RegistrationOutboxWorkerBootstrap(
        identity_database_url="identity-secret",
        worker_database_url="worker-secret",
        engine_factory=lambda *_args, **_kwargs: next(engines),
        session_factory_builder=lambda **kwargs: kwargs["bind"],
        composition_factory=lambda **_kwargs: object(),
        runtime_factory=lambda _composition: object(),
        clock=lambda: datetime.now(timezone.utc),
        uuid_generator=object(),
    )

    with pytest.raises(RuntimeError, match="task failed"):
        async with bootstrap:
            raise RuntimeError("task failed")

    assert disposed == ["worker", "identity"]


def test_Bootstrap环境边界拒绝非Disposable目标(monkeypatch):
    from app.tasks.registration_outbox_worker_bootstrap import (
        RegistrationOutboxWorkerBootstrap,
    )

    monkeypatch.setenv("KG_TEST_ENVIRONMENT", "production")
    monkeypatch.setenv("KG_IDENTITY_APPLICATION_DATABASE_URL", "hidden-a")
    monkeypatch.setenv("KG_DELIVERY_WORKER_DATABASE_URL", "hidden-b")

    with pytest.raises(
        RuntimeError,
        match="registration worker database target is not disposable",
    ):
        RegistrationOutboxWorkerBootstrap.from_environment()


def test_Bootstrap连接后复核数据库Sentinel与双角色身份():
    asyncio.run(_assert_connected_database_identity())


async def _assert_connected_database_identity():
    from app.tasks.registration_outbox_worker_bootstrap import (
        _validate_connected_target,
    )

    class Result:
        def __init__(self, values):
            self._values = values

        def one(self):
            return self._values

    class Connection:
        def __init__(self, values):
            self._values = values

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, statement):
            assert "shobj_description" in str(statement)
            assert "current_user" in str(statement)
            return Result(self._values)

    class Engine:
        def __init__(self, values):
            self._values = values

        def connect(self):
            return Connection(self._values)

    expected = {
        "expected_database_name": "kg_it_test_run",
        "expected_sentinel": "kg-test-disposable:test_run",
        "expected_role": "kg_ci_app_test_run",
    }
    await _validate_connected_target(
        Engine(
            (
                "kg_ci_app_test_run",
                "kg_it_test_run",
                "kg-test-disposable:test_run",
            )
        ),
        **expected,
    )
    with pytest.raises(
        RuntimeError,
        match="registration worker database identity is unverified",
    ):
        await _validate_connected_target(
            Engine(
                (
                    "kg_ci_migration_test_run",
                    "kg_it_test_run",
                    "kg-test-disposable:test_run",
                )
            ),
            **expected,
        )


def test_本地RabbitMQ清理在Sentinel不匹配时仍删除精确容器():
    events = []

    def docker(*arguments):
        events.append(arguments)
        if arguments[:3] == (
            "inspect",
            "--format",
            "{{json .Config.Labels}}",
        ):
            return json.dumps({"kg.test.sentinel": "unexpected"})
        if arguments[:3] == (
            "network",
            "inspect",
            "--format",
        ):
            return json.dumps({"kg.test.sentinel": "unexpected"})
        return ""

    with pytest.raises(
        AssertionError, match="Disposable RabbitMQ cleanup failed"
    ):
        _cleanup_local_rabbitmq(
            container_name="kg-reg-0123456789ab",
            network_name="kg-reg-0123456789ab-network",
            sentinel="kg-registration-disposable:0123456789ab",
            container_created=True,
            network_created=True,
            docker=docker,
        )
    assert ("stop", "kg-reg-0123456789ab") in events
    assert ("rm", "kg-reg-0123456789ab") in events
    assert (
        "network",
        "rm",
        "kg-reg-0123456789ab-network",
    ) not in events


def test_Beat观察使用有上限条件轮询并保持Registration专用队列():
    depths = iter((0, 0, 1))

    class Process:
        def poll(self):
            return None

    events = []
    _wait_for_beat_message(
        Process(),
        "kg-reg-0123456789ab",
        "registration",
        timeout=2,
        queue_depth=lambda container, queue: (
            events.append((container, queue)) or next(depths)
        ),
        sleep=lambda _seconds: None,
    )
    assert events == [
        ("kg-reg-0123456789ab", "registration"),
        ("kg-reg-0123456789ab", "registration"),
        ("kg-reg-0123456789ab", "registration"),
    ]


def test_Beat提前退出立即阻断且诊断不包含运行Secret():
    class Process:
        def poll(self):
            return 3

    with pytest.raises(
        AssertionError,
        match="Celery Beat exited before publishing registration task",
    ) as caught:
        _wait_for_beat_message(
            Process(),
            "kg-reg-0123456789ab",
            "registration",
            timeout=2,
            queue_depth=lambda *_args: 0,
            sleep=lambda _seconds: None,
        )
    public = str(caught.value).lower()
    for forbidden in ("amqp://", "password", "credential", "database_url"):
        assert forbidden not in public


if (
    os.getenv("KG_RUN_PG_INTEGRATION") == "1"
    and os.getenv("KG_RUN_RABBITMQ_INTEGRATION") == "1"
):

    def test_真实独立CeleryWorker进程Bootstrap与Beat():
        from alembic import command

        from app.tasks.celery_app import (
            DISPATCH_TASK_NAME,
            REGISTRATION_QUEUE,
            create_celery_app,
        )
        from tests.integration.conftest import (
            PgDatabase,
            _build_alembic_config,
            _grant_test_role_permissions,
        )
        from tests.integration.test_身份注册持久发件箱数据库合同 import (
            EVENT_ID,
            _production_writer_round_trip,
            _with_connection,
        )

        migration_url = os.environ["KG_TEST_MIGRATION_DATABASE_URL"]
        command.upgrade(_build_alembic_config(migration_url), "head")
        _grant_test_role_permissions(PgDatabase(migration_url))

        suffix = secrets.token_hex(6)
        container_name = os.getenv(
            "KG_RABBITMQ_CONTAINER_NAME", f"kg-reg-{suffix}"
        )
        network_name = os.getenv(
            "KG_RABBITMQ_NETWORK_NAME", f"kg-reg-{suffix}-network"
        )
        sentinel = os.getenv(
            "KG_RABBITMQ_SENTINEL", f"kg-registration-disposable:{suffix}"
        )
        beat_schedule = (
            Path(__file__).resolve().parents[1]
            / f"celerybeat-registration-{suffix}"
        )
        assert re.fullmatch(r"kg-reg-[0-9a-f]{12}", container_name)

        rabbit_user = f"kg_{secrets.token_hex(8)}"
        rabbit_password = secrets.token_urlsafe(36)
        broker_url = os.getenv("KG_CELERY_BROKER_URL")
        processes = []
        container_created = False
        network_created = False
        lock_thread = None
        release_lock = None
        try:
            if broker_url is None:
                _docker(
                    "network",
                    "create",
                    "--label",
                    f"kg.test.sentinel={sentinel}",
                    network_name,
                )
                network_created = True
                _docker(
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "--network",
                    network_name,
                    "--label",
                    f"kg.test.sentinel={sentinel}",
                    "-e",
                    f"RABBITMQ_DEFAULT_USER={rabbit_user}",
                    "-e",
                    f"RABBITMQ_DEFAULT_PASS={rabbit_password}",
                    "-p",
                    "127.0.0.1::5672",
                    "--health-cmd",
                    "rabbitmq-diagnostics -q ping",
                    "--health-interval",
                    "2s",
                    "--health-timeout",
                    "5s",
                    "--health-retries",
                    "30",
                    "rabbitmq:3-management-alpine",
                )
                container_created = True
                _wait_for_rabbitmq(container_name)
                port_mapping = _docker("port", container_name, "5672/tcp")
                port = port_mapping.rsplit(":", 1)[-1]
                broker_url = (
                    f"amqp://{quote(rabbit_user)}:{quote(rabbit_password)}@"
                    f"127.0.0.1:{port}//"
                )

            child_env = os.environ.copy()
            child_env.update(
                {
                    "KG_CELERY_BROKER_URL": broker_url,
                    "KG_CELERY_TEST_RESULT_BACKEND": "rpc",
                    "KG_IDENTITY_APPLICATION_DATABASE_URL": os.environ[
                        "KG_TEST_DATABASE_URL"
                    ],
                    "KG_DELIVERY_WORKER_DATABASE_URL": os.environ[
                        "KG_TEST_DELIVERY_WORKER_DATABASE_URL"
                    ],
                }
            )
            os.environ.update(
                {
                    "KG_CELERY_TEST_RESULT_BACKEND": "rpc",
                    "KG_IDENTITY_APPLICATION_DATABASE_URL": child_env[
                        "KG_IDENTITY_APPLICATION_DATABASE_URL"
                    ],
                    "KG_DELIVERY_WORKER_DATABASE_URL": child_env[
                        "KG_DELIVERY_WORKER_DATABASE_URL"
                    ],
                }
            )
            sender = create_celery_app(broker_url=broker_url)

            worker_a = _start_worker("registration-a@%h", child_env)
            processes.append(worker_a)
            _wait_for_worker_count(sender, 1)
            owner_a = _send_dispatch(
                sender, DISPATCH_TASK_NAME, REGISTRATION_QUEUE
            )["worker_ref"]

            _stop_process(worker_a)
            processes.remove(worker_a)
            worker_b = _start_worker("registration-b@%h", child_env)
            processes.append(worker_b)
            _wait_for_worker_count(sender, 1)
            owner_b = _send_dispatch(
                sender, DISPATCH_TASK_NAME, REGISTRATION_QUEUE
            )["worker_ref"]
            assert owner_a != owner_b

            asyncio.run(_production_writer_round_trip())
            lock_acquired = threading.Event()
            release_lock = threading.Event()
            lock_thread = threading.Thread(
                target=_hold_member_no_table_lock,
                args=(
                    migration_url,
                    lock_acquired,
                    release_lock,
                ),
                daemon=True,
            )
            lock_thread.start()
            assert lock_acquired.wait(timeout=20)
            interrupted_result = sender.send_task(
                DISPATCH_TASK_NAME,
                queue=REGISTRATION_QUEUE,
                ignore_result=False,
            )
            processing = _wait_for_outbox_state(
                os.environ["KG_TEST_DELIVERY_WORKER_DATABASE_URL"],
                EVENT_ID,
                lambda state: (
                    state["status"] == "processing"
                    and state["lease_owner"] == owner_b
                ),
            )
            time.sleep(35)
            heartbeated = _read_outbox_state(
                os.environ["KG_TEST_DELIVERY_WORKER_DATABASE_URL"], EVENT_ID
            )
            assert heartbeated["locked_until"] > processing["locked_until"]

            worker_b.kill()
            worker_b.wait(timeout=20)
            processes.remove(worker_b)
            release_lock.set()
            lock_thread.join(timeout=20)
            assert not lock_thread.is_alive()

            worker_a = _start_worker("registration-a@%h", child_env)
            processes.append(worker_a)
            _wait_for_worker_count(sender, 1)
            unexpired = _send_dispatch(
                sender, DISPATCH_TASK_NAME, REGISTRATION_QUEUE
            )
            assert unexpired["claimed"] == 0

            asyncio.run(
                _expire_outbox_lease(
                    migration_url,
                    EVENT_ID,
                )
            )
            worker_c = _start_worker("registration-c@%h", child_env)
            processes.append(worker_c)
            _wait_for_worker_count(sender, 2)
            takeover_results = [
                sender.send_task(
                    DISPATCH_TASK_NAME,
                    queue=REGISTRATION_QUEUE,
                    ignore_result=False,
                )
                for _ in range(2)
            ]
            takeover_payloads = [
                result.get(timeout=30, disable_sync_subtasks=False)
                for result in takeover_results
            ]
            assert sum(item["claimed"] for item in takeover_payloads) == 1
            assert sum(item["delivered"] for item in takeover_payloads) == 1
            final_state = _read_outbox_state(
                os.environ["KG_TEST_DELIVERY_WORKER_DATABASE_URL"], EVENT_ID
            )
            assert final_state["status"] == "delivered"
            assert final_state["lease_generation"] == (
                processing["lease_generation"] + 1
            )
            assert asyncio.run(
                _old_lease_ack_is_fenced(
                    os.environ["KG_TEST_DELIVERY_WORKER_DATABASE_URL"],
                    EVENT_ID,
                    owner_b,
                    processing["lease_generation"],
                )
            )
            assert asyncio.run(_identity_object_counts(migration_url)) == (
                1,
                1,
                1,
                1,
                1,
            )

            _stop_process(worker_a)
            processes.remove(worker_a)
            _stop_process(worker_c)
            processes.remove(worker_c)
            beat = _start_beat(child_env, beat_schedule)
            processes.append(beat)
            _wait_for_beat_message(
                beat,
                container_name,
                REGISTRATION_QUEUE,
                timeout=30,
            )
            _stop_process(beat)
            processes.remove(beat)

            worker_a = _start_worker("registration-a@%h", child_env)
            processes.append(worker_a)
            _wait_for_worker_count(sender, 1)
            _wait_for_queue_empty(container_name, REGISTRATION_QUEUE)
        finally:
            if release_lock is not None:
                release_lock.set()
            if lock_thread is not None:
                lock_thread.join(timeout=20)
            for process in reversed(processes):
                _stop_process(process)
            for name in (
                "KG_CELERY_TEST_RESULT_BACKEND",
                "KG_IDENTITY_APPLICATION_DATABASE_URL",
                "KG_DELIVERY_WORKER_DATABASE_URL",
            ):
                os.environ.pop(name, None)
            for suffix_name in (".dat", ".dir", ".bak"):
                Path(f"{beat_schedule}{suffix_name}").unlink(missing_ok=True)
            _cleanup_local_rabbitmq(
                container_name=container_name,
                network_name=network_name,
                sentinel=sentinel,
                container_created=container_created,
                network_created=network_created,
            )


def _docker(*arguments):
    completed = subprocess.run(
        ("docker", *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError("Disposable container command failed")
    return completed.stdout.strip()


def _cleanup_local_rabbitmq(
    *,
    container_name,
    network_name,
    sentinel,
    container_created,
    network_created,
    docker=_docker,
):
    cleanup_failed = False
    if container_created:
        try:
            labels = json.loads(
                docker(
                    "inspect",
                    "--format",
                    "{{json .Config.Labels}}",
                    container_name,
                )
            )
            if labels.get("kg.test.sentinel") != sentinel:
                cleanup_failed = True
        except Exception:
            cleanup_failed = True
        for command in (("stop", container_name), ("rm", container_name)):
            try:
                docker(*command)
            except Exception:
                cleanup_failed = True
    if network_created:
        try:
            labels = json.loads(
                docker(
                    "network",
                    "inspect",
                    "--format",
                    "{{json .Labels}}",
                    network_name,
                )
            )
            if labels.get("kg.test.sentinel") == sentinel:
                docker("network", "rm", network_name)
            else:
                cleanup_failed = True
        except Exception:
            cleanup_failed = True
    if cleanup_failed:
        raise AssertionError("Disposable RabbitMQ cleanup failed")


def _wait_for_rabbitmq(container_name):
    _wait_for(
        lambda: _docker(
            "inspect", "--format", "{{.State.Health.Status}}", container_name
        )
        == "healthy",
        "Disposable RabbitMQ did not become healthy",
        timeout=60,
    )


def _worker_command(nodename):
    return (
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.tasks.celery_app:celery_app",
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--prefetch-multiplier=1",
        "--queues=registration",
        f"--hostname={nodename}",
        "--loglevel=ERROR",
        "--without-gossip",
        "--without-mingle",
    )


def _start_worker(nodename, environment):
    return subprocess.Popen(
        _worker_command(nodename),
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _start_beat(environment, schedule_path):
    return subprocess.Popen(
        (
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.tasks.celery_app:celery_app",
            "beat",
            "--loglevel=ERROR",
            f"--schedule={schedule_path}",
        ),
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_worker_count(app, count):
    def ready():
        replies = app.control.ping(timeout=1) or []
        return len(replies) == count

    _wait_for(ready, "Celery worker process did not become ready", timeout=30)


def _send_dispatch(app, task_name, queue):
    result = app.send_task(task_name, queue=queue, ignore_result=False)
    payload = result.get(timeout=30, disable_sync_subtasks=False)
    assert set(payload) == {
        "worker_ref",
        "claimed",
        "delivered",
        "retried",
        "review_required",
        "dead_lettered",
        "lease_lost",
    }
    return payload


def _hold_member_no_table_lock(database_url, acquired, release):
    async def hold():
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "LOCK TABLE identity.member_no_allocation "
                        "IN ACCESS EXCLUSIVE MODE"
                    )
                )
                acquired.set()
                await asyncio.to_thread(release.wait)
        finally:
            await engine.dispose()

    asyncio.run(hold())


def _read_outbox_state(database_url, event_id):
    async def read():
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT status, lease_owner, locked_until, "
                            "lease_generation FROM "
                            "public.registration_verified_outbox "
                            "WHERE event_id=:event_id"
                        ),
                        {"event_id": event_id},
                    )
                ).mappings().one()
                return dict(row)
        finally:
            await engine.dispose()

    return asyncio.run(read())


def _wait_for_outbox_state(database_url, event_id, predicate):
    state = None

    def ready():
        nonlocal state
        state = _read_outbox_state(database_url, event_id)
        return predicate(state)

    _wait_for(ready, "Celery worker did not establish the expected lease", timeout=30)
    return state


async def _expire_outbox_lease(database_url, event_id):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE public.registration_verified_outbox "
                    "SET locked_until=now() - interval '1 second' "
                    "WHERE event_id=:event_id AND status='processing'"
                ),
                {"event_id": event_id},
            )
    finally:
        await engine.dispose()


async def _old_lease_ack_is_fenced(
    database_url, event_id, lease_owner, lease_generation
):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(
                text(
                    "UPDATE public.registration_verified_outbox "
                    "SET updated_at=now() WHERE event_id=:event_id "
                    "AND lease_owner=:lease_owner "
                    "AND lease_generation=:lease_generation"
                ),
                {
                    "event_id": event_id,
                    "lease_owner": lease_owner,
                    "lease_generation": lease_generation,
                },
            )
            return result.rowcount == 0
    finally:
        await engine.dispose()


async def _identity_object_counts(database_url):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            values = []
            for table in (
                "identity.member_no_allocation",
                "identity.member",
                "identity.user_member_self_link",
                "identity.registration_bootstrap_record",
                "public.registration_verified_outbox",
            ):
                values.append(
                    (
                        await connection.execute(
                            text(f"SELECT count(*) FROM {table}")
                        )
                    ).scalar_one()
                )
            return tuple(values)
    finally:
        await engine.dispose()


def _rabbitmq_queue_depth(container_name, queue):
    output = _docker(
        "exec",
        container_name,
        "rabbitmqctl",
        "-q",
        "list_queues",
        "name",
        "messages",
    )
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == queue:
            return int(fields[1])
    return 0


def _wait_for_beat_message(
    process,
    container_name,
    queue,
    *,
    timeout,
    queue_depth=_rabbitmq_queue_depth,
    sleep=time.sleep,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                "Celery Beat exited before publishing registration task"
            )
        if queue_depth(container_name, queue) >= 1:
            return
        sleep(0.25)
    raise AssertionError(
        "Celery Beat did not publish to the registration queue before timeout"
    )


def _wait_for_queue_empty(container_name, queue):
    _wait_for(
        lambda: _rabbitmq_queue_depth(container_name, queue) == 0,
        "Celery Beat task was not consumed",
        timeout=30,
    )


def _stop_process(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def _wait_for(predicate, message, *, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise AssertionError(message)


def test_真实Writer准备路径必须经过人工审核Authority边界():
    path = (
        Path(__file__).resolve().parent
        / "integration"
        / "test_身份注册持久发件箱数据库合同.py"
    )
    file_source = path.read_text(encoding="utf-8")
    tree = ast.parse(file_source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_production_writer_round_trip"
    )
    source = ast.get_source_segment(file_source, function)
    assert source is not None
    message = "Verified transition integration path bypasses authority boundary"
    assert "P1VerificationTransitionCommand" not in source, message
    assert (
        "create_manual_identity_review_verified_transition_service" in source
    ), message
