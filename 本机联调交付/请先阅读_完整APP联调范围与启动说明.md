# 请先阅读：完整 APP 联调基础包

本包状态固定为 `FOUNDATION ONLY / NOT FULL BUSINESS READY`。它用于 Windows localhost + Docker Desktop 的源码校验、Fresh 依赖准备、API/数据库/RabbitMQ/八类 Worker/beat/两个 Web 入口启停与重启持久性验证，不代表 G2–G5 业务旅程或 APP 真机完成。

顺序：`前置软件检查.ps1` → `准备本机联调.ps1` → `启动本机联调.ps1` → `检查本机联调状态.ps1`。`停止`保留数据；`重启`验证保留；只有携带当前 run_id 的 `显式重置`才删除本任务资源。

不自动安装 PowerShell、Docker Desktop、Python/uv、Node/npm；缺失时稳定失败。所有监听为 `127.0.0.1`。准备阶段使用 S1 已审阅的 digest-pinned ClamAV 和任务专属签名卷；Scanner 运行或健康合同不满足时显示 BLOCKED，不伪装成功。不得把状态目录、日志或随机凭据交付他人。
