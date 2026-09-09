from __future__ import annotations

import os

from scripts.check_worker_readiness import probe_worker


def test_C2_3_精确目标Worker在自身进程证明队列任务与数据库就绪() -> None:
    worker_kind = os.environ["KG_C2_3_WORKER_KIND"]
    hostname = os.environ["KG_C2_3_WORKER_HOSTNAME"]

    assert probe_worker(worker_kind=worker_kind, hostname=hostname) == {
        "status": "READY",
        "worker_kind": worker_kind,
    }
