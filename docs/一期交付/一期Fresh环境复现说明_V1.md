# 一期 Fresh 环境复现说明 V1

## 文档状态

- 本文是可复现步骤合同，不是部署记录。
- 只记录环境变量名称、格式和生成方法；禁止记录值、带凭据 URL、Token、PII 或 PHI。
- `CI-reproducible`、`local deployment-ready`、`DEPLOYED` 与 `APP_HANDOFF_READY` 是不同状态，不能互相推导。

## 冻结工具链与来源

1. 冻结 repository、commit SHA、tree SHA、branch、PR 与工作树状态。
2. Python 使用仓库 `backend/pyproject.toml` 与 `backend/uv.lock`；先执行 `uv lock --check`，再执行 `uv sync --frozen`。
3. Ruff 使用 `pyproject.toml` 冻结版本与规则，不使用 `latest`。
4. Frontend 使用 Workflow 冻结的 Node major、`frontend/package.json` 与 `frontend/package-lock.json`；只执行 `npm ci`，不得用 `npm install` 改写 lock。
5. 记录 Python、uv、Ruff、Node、npm 的实际版本和上述文件 SHA-256。

## Windows localhost

1. 从 Fresh checkout 开始，确认 tracked/staged 为 0；既有 untracked 不作为权威证据。
2. 在 `backend` 目录执行锁校验、frozen sync、定向测试和完整非数据库测试。
3. 在 `frontend` 目录执行 `npm ci`、`npm run build`、正式 Vitest JUnit、base 与 slice4/5/6/7 lint、源码 PII 扫描。
4. Windows 路径、CRLF、PowerShell quoting 与 `.cmd` 可执行发现必须单独记录；不得把 Windows runner 编排失败描述为产品失败。
5. 本机步骤成功只证明 localhost 可复现，不自动证明 `local deployment-ready`。

## CI Linux

1. 使用 `.github/workflows/p2-foundation-ci.yml` 中精确 action commit、Node 配置与锁文件。
2. Repository Safety、Backend Unit、Backend Integration、Frontend Build 四个 job 必须全部 SUCCESS。
3. Frontend Build 必须真实执行 build、Vitest、全部冻结 lint、源码 PII 与交付产物扫描。
4. 下载全部 backend/frontend JUnit artifacts，按文件分别递归汇总 testcase；不得重复相加同一节点。
5. CI 成功只能标记 `CI-reproducible` 或候选 `CI_VERIFIED`，不能标记已部署。

## Fresh Disposable 服务

1. PostgreSQL 使用任务专属数据库、角色和 run sentinel；变量仅记录 `KG_TEST_RUN_ID`、数据库/角色变量名及安全格式，不记录值。
2. 从当前单一 Migration Head 执行 upgrade；风险相称时执行 downgrade/re-upgrade，并核验历史 revision Hash。
3. RabbitMQ、private-file storage 与 Worker 使用任务专属端口、队列、目录、容器、网络和卷；不复用共享实例。
4. 只写入合成数据；禁止连接真实或共享数据库、对象存储、Scanner 或外部服务。
5. 测试完成后删除任务数据库、角色、RabbitMQ、Worker、容器、网络、卷与临时目录。

## 标准验证顺序

1. Source、tree、Migration Head、OpenAPI、Python lock 与 frontend lock 冻结。
2. Backend 定向、完整非数据库、Fresh PostgreSQL、正式 Integration 与风险相称专项节点。
3. Frontend `npm ci`、build、Vitest JUnit、base+slice4/5/6/7 lint、源码 PII、交付 artifact 扫描。
4. 递归 JUnit、敏感扫描、artifact Hash 与资源清理。
5. 独立复审 Critical/Important/Minor 为 0/0/0 后才形成候选提交。

## 证据与清理

- Evidence 只包含计数、布尔、稳定错误码、commit/tree/run/artifact Hash 和工具版本。
- JUnit、dist、诊断 summary 与实际 Manifest 均须通过交付敏感扫描；现有 `npm run pii:scan` 仅负责其冻结源码子集。
- 失败证据保留原始计数并标记 REJECTED/EXPECTED RED，不能用文件名冒充 GREEN。
- 必须记录数据库、角色、RabbitMQ、Worker、container、network、volume、process 与临时 artifact 残留为 0。

## 能力边界

- 仓库当前没有由本文证明的生产 Docker image、Compose 部署或流量切换能力。
- CI 全绿不等于可部署；`DEPLOYABLE` 还需要环境、配置、备份、回滚和安全门禁。
- `DEPLOYED` 必须有真实部署授权与证据；本文不授权。
- APP 源码可追溯性与正式接线仍按既有延期风险处理，本文不生成 APP 正式交接。
