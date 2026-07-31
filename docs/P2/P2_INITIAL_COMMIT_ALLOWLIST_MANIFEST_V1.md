# P2 Initial Commit Allowlist Manifest V1

> 项目：KG_康邻智慧健康平台  
> Repository Root：`E:/nas/JR/SynologyDrive/KG_康邻/研发/平台/KG_20260727`  
> 基线来源：`P2_PRE_INITIALIZATION_SECURITY_SCAN_EXECUTION_V1.md`  
> 候选快照日期：2026-07-31（Asia/Shanghai）  
> 文档性质：首次提交允许范围，非Stage或Commit授权

## Current Status

```text
P2 Initial Commit Allowlist Manifest:
REVIEW READY

P2 Git Initial Commit:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

本Manifest将首次Git基线拆分为Commit 0（Repository Policy Baseline）与Commit 1（P1 Code Baseline）。任何文件只有同时满足“路径在Allowlist内、内容通过安全扫描、Hash属于批准快照”三个条件，才具备后续Stage资格。

当前Repository Root有228个未忽略候选文件。已扫描快照聚合SHA-256为：

```text
63fa36de8c5091753cf0bc4d8cc4ba9d31cc9ef6c9fbe142d9612995ff398940
```

该Hash是复扫证据，不是Commit ID。文件发生任何变化后必须重新生成Manifest与扫描证据。

---

# 1. Commit 0 Allowlist

## 1.1 目标

Commit 0只建立Repository Governance、安全边界和仓库说明，不包含Backend、Frontend、Migration、测试或P2业务实现。

## 1.2 当前已扫描文件

| 路径 | 类别 | 当前状态 | 准入条件 |
|---|---|---|---|
| `.gitignore` | Repository Governance / 安全排除规则 | 已扫描 | 安全与技术Reviewer确认规则完整 |
| `README.md` | Repository入口与范围说明 | 已扫描 | 确认不含Credential、PII/PHI及过时运行指令 |

当前Commit 0已扫描候选：2个文件，4,174 bytes。

## 1.3 Repository Governance候选文档

以下治理和安全文档当前位于`KG_28/docs/P2`，不在Repository Root，也不属于已扫描的228文件快照。它们只能在完成受控导入、重新扫描和新Hash审批后进入Commit 0：

| 目标路径候选 | 用途 | 当前状态 |
|---|---|---|
| `docs/P2/P2_GIT_CI_BASELINE_REVIEW_V1.md` | Git/CI基线治理 | Pending Import and Rescan |
| `docs/P2/P2_GIT_REPOSITORY_INITIALIZATION_PLAN_V1.md` | Repository初始化规则 | Pending Import and Rescan |
| `docs/P2/P2_PRE_INITIALIZATION_SECURITY_SCAN_PLAN_V1.md` | 首次Commit安全规则 | Pending Import and Rescan |
| `docs/P2/P2_PRE_INITIALIZATION_SECURITY_SCAN_EXECUTION_V1.md` | 安全扫描证据 | Pending Import and Rescan |
| `docs/P2/P2_HIGH_FINDING_REMEDIATION_REPORT_V1.md` | High Finding处置证据 | Pending Import and Rescan |
| `docs/P2/P2_INITIAL_COMMIT_ALLOWLIST_MANIFEST_V1.md` | Allowlist治理基线 | Pending Import and Rescan |

未完成导入和复扫前，不得为了满足“Governance文档入库”而直接Stage外部路径或复制文件。

## 1.4 Commit 0禁止混入

- `backend/**`；
- `frontend/**`；
- 根`/scripts/**`；
- Migration与测试；
- 依赖、构建产物或业务功能变更；
- 未在1.2或经复扫批准后的1.3中列出的文件。

## 1.5 Commit 0 Gate

```text
Commit 0 Allowlist:
DEFINED — NOT APPROVED
```

Commit 0不得执行，直到治理文档导入策略、最终文件级Hash和人工审批完成。

---

# 2. Commit 1 Allowlist

## 2.1 目标

Commit 1固定当前P1 Backend、Frontend、Migration、Tests与必要工程资料，形成P2 Phase 1开始前的可回滚代码基线。Commit 1不得引入新的P2业务实现。

## 2.2 Backend Allowlist

| 路径规则 | 文件数 | Bytes | 允许内容 |
|---|---:|---:|---|
| `backend/app/**`（不含`backend/app/migrations/**`） | 61 | 123,821 | P1应用入口、Core、Domain模块、任务与现有模型 |
| `backend/app/migrations/**` | 9 | 18,067 | 已存在Alembic历史，不允许新增Migration |
| `backend/tests/**` | 85 | 553,394 | Unit与Integration测试源代码，不含测试产物 |
| `backend/scripts/**` | 2 | 27,991 | 已扫描的P1 Seed及Backend测试脚本 |
| `backend/.gitignore` | 1 | 76 | Backend局部忽略规则 |
| `backend/alembic.ini` | 1 | 77 | Alembic非Secret配置 |
| `backend/pyproject.toml` | 1 | 757 | Backend依赖与项目元数据 |
| **Backend合计** | **160** | **724,183** | 当前P1 Backend基线 |

### Migration精确清单

- `backend/app/migrations/README.md`
- `backend/app/migrations/env.py`
- `backend/app/migrations/script.py.mako`
- `backend/app/migrations/versions/20260727_0001_empty_database_baseline.py`
- `backend/app/migrations/versions/20260728_0002_core_tenant_user_baseline.py`
- `backend/app/migrations/versions/20260728_0003_f001_tenant_onboarding_models.py`
- `backend/app/migrations/versions/20260728_0004_tenant_attachment.py`
- `backend/app/migrations/versions/20260728_0005_f002_health_profile.py`
- `backend/app/migrations/versions/20260728_0006_f003_health_indicator.py`

任何新Migration、SQL文件或Schema变化均不属于本Manifest。

## 2.3 Frontend Allowlist

| 路径/文件 | 文件数 | Bytes | 允许内容 |
|---|---:|---:|---|
| `frontend/src/**` | 47 | 67,060 | P1 Platform Web与Institution Web源代码 |
| `frontend/.gitignore` | 1 | — | Frontend局部忽略规则 |
| `frontend/index.html` | 1 | — | Vite入口 |
| `frontend/package.json` | 1 | — | 依赖与脚本声明 |
| `frontend/package-lock.json` | 1 | — | 依赖锁文件 |
| `frontend/postcss.config.js` | 1 | — | 构建配置 |
| `frontend/tailwind.config.ts` | 1 | — | 样式配置 |
| `frontend/tsconfig.app.json` | 1 | — | TypeScript配置 |
| `frontend/tsconfig.json` | 1 | — | TypeScript配置 |
| `frontend/tsconfig.node.json` | 1 | — | TypeScript Node配置 |
| `frontend/vite.config.ts` | 1 | — | Vite配置 |
| **Frontend合计** | **57** | **144,902** | 当前P1 Frontend基线 |

配置文件必须保持不含真实API Secret、Token或环境Credential。

## 2.4 Root Scripts Allowlist

| 路径规则 | 文件数 | Bytes | 允许内容 |
|---|---:|---:|---|
| `scripts/**` | 9 | 107,219 | 已扫描的治理、映射构建和验证脚本 |

Root Scripts不得携带NAS外部敏感资料、生成Artifact、Credential或数据库导出。

## 2.5 Tests Allowlist规则

允许：

- Unit测试源文件；
- Integration测试源文件；
- 明确的合成测试身份和健康数据；
- 运行时随机生成的测试Secret；
- Test Fixture结构与Mock实现。

禁止：

- 真实客户、员工、会员或患者数据；
- 固定可复用UAT/Production密码；
- 测试运行产生的数据库、日志、截图、Coverage与Cache；
- 指向共享UAT数据库的破坏性默认配置；
- 测试过程中导出的数据文件。

## 2.6 必要Docs Allowlist

当前Repository Root中`docs/**`候选文件数为0。因此，本Manifest不批准任何Docs进入当前Commit 1快照。

未来必要Docs仅允许按以下类别建立独立文件级清单后纳入：

- P1产品闭环与UAT Runbook；
- 已脱敏的UAT结论，不默认包含截图；
- P2已批准架构与实施Gate；
- Repository Governance和安全证据；
- 与当前Commit可追溯关系直接相关的说明。

必要Docs迁入后必须重新进行Secret、PII/PHI、大文件、数据库Artifact和Hash扫描；不得使用`docs/**`作为无条件通配Allowlist。

## 2.7 Commit 1汇总

| 范围 | 文件数 | Bytes |
|---|---:|---:|
| Backend | 160 | 724,183 |
| Frontend | 57 | 144,902 |
| Root Scripts | 9 | 107,219 |
| 必要Docs | 0 | 0 |
| **Commit 1候选合计** | **226** | **976,304** |

Commit 0与Commit 1当前候选总计：228个文件，980,478 bytes。

```text
Commit 1 Allowlist:
DEFINED — NOT APPROVED
```

---

# 3. Explicit Denylist

下列内容不因路径位于Backend、Frontend、Tests、Scripts或Docs下而获得准入资格。

## 3.1 Secret与Credential

- `.env`、`.env.*`真实环境文件；
- password、Token、API Key、JWT Secret；
- 数据库Credential与带认证信息的连接串；
- SSH/PEM Private Key；
- P12、PFX及含私钥证书；
- Service Account、Cloud Credential、CI Secret；
- GitHub密码、PAT、Deploy Key；
- Cookie、Session、Authorization Header和临时签名URL。

允许提交的`.env.example`只能包含不可用占位值，并须独立扫描。

## 3.2 PII与PHI

- 真实姓名、手机号、身份证件、地址和银行卡；
- 客户、员工、会员、Therapist名单；
- 真实Health Profile、Health Fact、Health Indicator；
- 检测、Assessment、Report、Plan和风险记录；
- 未脱敏UAT截图、日志、录屏与附件；
- 可重新识别个人的数据组合。

## 3.3 Database Artifact

- SQL Dump与批量数据导出；
- `.dump`、`.backup`、`.bak`；
- SQLite、DB文件；
- PostgreSQL Data Directory、WAL与Volume；
- TimescaleDB Chunk、快照和备份；
- CSV/JSON/Excel真实查询结果；
- UAT、Development、Testing、Staging、Production数据库副本。

已有Alembic Migration源码不属于数据库Artifact，但任何新增Migration必须走独立Gate。

## 3.4 Generated Artifact

- `.venv/`、`venv/`；
- `node_modules/`；
- `dist/`、`build/`、Coverage；
- `__pycache__/`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`；
- `*.pyc`、`*.egg-info/`、`*.tsbuildinfo`；
- 日志、Crash Dump、临时文件与运行Artifact；
- 自动生成但不可复现或不需要审查的文件。

## 3.5 Backup、Archive与Large Binary

- ZIP、RAR、7Z、TAR、GZ；
- 本地Backup目录和历史副本；
- Synology冲突副本和同步临时文件；
- 未批准模型、权重、图片、视频和二进制；
- 大于20 MiB的未批准文件；
- 5–20 MiB但没有Owner和必要性说明的文件。

## 3.6 范围Denylist

- Repository Root之外的任何文件；
- 未经导入扫描的`KG_28/docs/P2`文件；
- 产品原型源目录与全部产品资料；
- Decision-003 Family Authorization实现；
- Decision-008 Health Fact Correction实现；
- Decision-009 Service Relationship实现；
- 任何P2 Phase 1业务代码。

---

# 4. Hash Evidence Requirement

## 4.1 当前证据

当前228文件候选快照已经完成两轮读取和Git Hash可读性验证：

```text
Candidate Files: 228
Readable Files: 228
Git Readable Files: 228
Read Failures: 0
Changed Files Between Passes: 0
Aggregate SHA-256:
63fa36de8c5091753cf0bc4d8cc4ba9d31cc9ef6c9fbe142d9612995ff398940
```

## 4.2 Stage前证据

未来获准Stage前必须重新生成：

- 每个允许文件的相对路径；
- 文件大小；
- SHA-256；
- Git Object Hash；
- ReparsePoint状态；
- Commit 0/Commit 1归属；
- 扫描报告版本；
- 执行时间、执行人和Reviewer。

## 4.3 Hash失效条件

发生以下任一情况，当前聚合Hash立即失效：

- 任一候选文件内容、路径或大小变化；
- 新增或删除文件；
- 导入任何Docs；
- `.gitignore`修改导致候选树改变；
- Synology同步更新或产生冲突副本；
- High Finding修复后再次修改相关文件；
- Stage Tree与批准清单不一致。

Hash失效后必须重新执行安全扫描，不得只重新计算Hash后直接Stage。

---

# 5. Review Approval Requirement

## 5.1 审批角色

| 角色 | 审批内容 |
|---|---|
| 工程负责人 | 文件范围、Commit拆分、P1代码完整性 |
| 安全负责人 | Secret、Credential、Denylist与Finding处置 |
| 合规/健康专业负责人 | PII/PHI、健康样例与Evidence边界 |
| P1维护负责人 | P1业务逻辑、Migration历史和UAT基线 |
| Repository Owner | Private仓库、权限、后续Remote与接管责任 |
| 文档Owner | Docs导入、权威来源和脱敏状态 |

## 5.2 Commit 0审批条件

- [ ] `.gitignore`人工复核通过
- [ ] `README.md`范围与运行说明复核通过
- [ ] Repository Governance文档是否导入已决定
- [ ] 若导入文档，已完成重新扫描和新Hash
- [ ] Commit 0精确文件清单已签署
- [ ] 无Secret、PII/PHI或数据库Artifact

## 5.3 Commit 1审批条件

- [ ] Backend 160文件清单已签署
- [ ] Frontend 57文件清单已签署
- [ ] Root Scripts 9文件清单已签署
- [ ] 9个Migration文件与Revision历史已确认
- [ ] Test数据被确认是合成数据
- [ ] FIND-SEC-001、FIND-SEC-002人工关闭
- [ ] 运行环境Credential已完成必要轮换
- [ ] 当前Backend回归中缺失治理文档的已知失败获得处置决定
- [ ] Commit 1精确Hash与Stage候选一致
- [ ] Decision-003/008/009仍为Blocked

## 5.4 审批结论

审批必须绑定具体Manifest Hash。口头批准、目录级批准或“仓库是Private”都不能替代文件级审批。

当前状态：

```text
Commit 0 Approval:
WAITING FOR HUMAN REVIEW

Commit 1 Approval:
WAITING FOR HUMAN REVIEW
```

---

# 6. Rollback Strategy

## 6.1 Stage前

若Allowlist评审失败：

- 保持0 Staged、0 Commit、0 Remote；
- 不删除或覆盖P1源文件；
- 修订Allowlist或Denylist；
- 重新生成候选快照与Hash；
- 重新执行受影响的安全扫描；
- 原审批和Hash标记失效。

## 6.2 Stage后、Commit前

未来若已获得Stage授权但发现范围不一致：

- 立即停止Commit；
- 仅撤销暂存状态，不回退工作区代码；
- 保存差异证据；
- 恢复到批准的文件级Allowlist；
- 重新复扫Stage Tree并再次审批。

本Manifest当前不授权执行上述Stage动作。

## 6.3 Commit后、Remote前

若未来Commit内容与批准Manifest不一致：

- 不得配置Remote或Push；
- 冻结本地仓库；
- 如涉及Secret，先撤销/轮换Credential；
- 由人工决定重建干净Initial Baseline；
- 不使用普通后续删除Commit掩盖敏感历史。

## 6.4 安全修复回滚限制

- 不得恢复数据库密码默认值；
- 不得恢复JWT Secret默认值；
- 不得恢复固定Seed/UAT密码；
- 若运行环境不兼容，应通过受控环境变量修复，不得降低Fail Closed要求。

---

# Final Decision

Commit 0和Commit 1的允许范围、Denylist、Hash证据和审批要求已经定义。当前治理文档尚未导入Repository Root，文件级人工审批和Credential轮换证据仍未完成，因此本Manifest只达到`REVIEW READY`。

```text
P2 Initial Commit Allowlist Manifest:
REVIEW READY

P2 Git Initial Commit:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

## Compliance Check

- [x] 未执行`git add`
- [x] 未执行`git commit`
- [x] 未创建或配置Remote
- [x] 未修改应用代码
- [x] 未修改数据库
- [x] 未引入新Migration
- [x] 未纳入Decision-003/008/009实现
- [x] Initial Commit继续保持`NOT OPEN`

**下一步仅允许人工评审Commit 0/Commit 1范围及文档导入策略。批准前不得Stage或Commit。**
