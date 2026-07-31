# P2 Git CI Baseline Review V1

> 项目：KG-康邻智慧健康平台  
> 阶段：P2 Phase 1 — Git / CI Baseline Review  
> 检查日期：2026-07-31  
> 检查性质：只读评审与未来基线设计  
> 仓库可见性要求：**Private Only**  
> 文档状态：**REVIEW READY**

## Executive Decision

```text
P2 Git CI Baseline Readiness:
NOT READY

P2 Git CI Baseline Review:
REVIEW READY

P2 Phase 1 Implementation:
NOT OPEN
```

Git客户端已安装，但KG根目录、代码目录、Backend、Frontend和KG_28文档目录均不是Git工作树，因此不存在可验证的Branch、HEAD Commit、工作区状态或Remote。项目级CI配置也未发现。当前无法建立Commit、Task、Architecture Document、Test Evidence、Artifact和Rollback之间的审计链。

用户指定的未来GitHub仓库Owner为`huberyxyh`，仓库必须为Private。本次不登录GitHub、不验证账号、不创建仓库，也不使用或记录对话中提供的密码。该密码已经暴露，应立即轮换；未来应使用组织批准的短期Token、SSH Key或受控App凭据，不得将账号密码写入Remote URL、配置、脚本、日志或CI Secret明文。

---

# 1. Current Git Status Review

## 1.1 Git客户端

```text
git version 2.45.1.windows.1
```

本机Git客户端可用。客户端存在不等于项目已经受Git管理。

## 1.2 候选目录检查

对以下目录执行只读Git工作树识别：

| 候选目录 | `.git`标记 | Git Worktree | Branch | HEAD Commit | Remote |
|---|---|---|---|---|---|
| `KG_康邻/` | 未发现 | No | 不存在 | 不存在 | 不存在 |
| `KG_康邻/研发/平台/` | 未发现 | No | 不存在 | 不存在 | 不存在 |
| `KG_20260727/` | 未发现 | No | 不存在 | 不存在 | 不存在 |
| `KG_20260727/backend/` | 未发现 | No | 不存在 | 不存在 | 不存在 |
| `KG_20260727/frontend/` | 未发现 | No | 不存在 | 不存在 | 不存在 |
| `KG_28/` | 未发现 | No | 不存在 | 不存在 | 不存在 |

`git rev-parse`和`git status`均不能识别这些目录为仓库。

## 1.3 Branch状态

当前没有Git工作树，因此：

- 无当前Branch；
- 无`main`；
- 无`develop`；
- 无Feature或Release Branch；
- 无Protected Branch证据；
- 无PR/Merge历史。

## 1.4 Commit状态

当前无法确认：

- 初始Commit；
- 当前HEAD SHA；
- 文件首次引入时间；
- 工作区是否存在未提交改动；
- P1 UAT版本对应哪个Commit；
- P2架构文档对应哪个Commit；
- Migration Revision对应哪个应用版本；
- 可回滚Commit或Release Tag。

文件时间戳和Synology同步历史不能替代Git Commit历史。

## 1.5 Remote状态

当前无Remote，因此无法确认：

- GitHub仓库是否存在；
- 仓库是否Private；
- `origin`指向；
- Owner和协作者；
- Branch Protection；
- Secret Scanning和访问审计；
- 远端备份及恢复能力。

## 1.6 Credential安全结论

- 本评审未使用用户提供的密码；
- 密码不得写入本文或任何本地/远端配置；
- 已在对话中出现的密码应视为已暴露并立即轮换；
- Git Remote不得包含明文Credential；
- Private仓库不能替代Secret治理；
- 在仓库初始化前必须执行Secret扫描和大文件/敏感文件排除评审。

## 1.7 Git状态结论

```text
Git Client:
AVAILABLE

Project Git Repository:
NOT FOUND

Branch / Commit / Remote Traceability:
NOT AVAILABLE
```

---

# 2. Repository Structure Review

## 2.1 当前代码主线

当前代码目录：

```text
E:/nas/JR/SynologyDrive/KG_康邻/研发/平台/KG_20260727/
```

结构：

```text
KG_20260727/
├─ backend/
├─ frontend/
├─ docs/
├─ scripts/
└─ README.md
```

这是最接近未来私有Monorepo的现有边界。

## 2.2 Backend

`backend/`包含：

- `app/`；
- `app/modules/`；
- `app/migrations/`；
- `tests/`和`tests/integration/`；
- `scripts/seed_p1_test_data.py`；
- `scripts/test-backend.ps1`；
- `pyproject.toml`与`alembic.ini`；
- 本地`.venv`、`.pytest_cache`及egg-info。

未来Git基线应纳入源码、测试、Migration历史、受控脚本和依赖声明；不应纳入`.venv`、缓存、构建产物、本地Secret或测试数据库数据。

## 2.3 Frontend

`frontend/`包含：

- `src/`；
- Vite/TypeScript配置；
- `package.json`与`package-lock.json`；
- `dist/`；
- `node_modules/`。

未来Git基线应纳入源码、配置、`package.json`和锁文件；不应纳入`node_modules`、`dist`、本地环境Secret或浏览器测试临时产物。

## 2.4 Docs

当前存在两个文档位置：

1. `KG_20260727/docs/`：项目章程、架构基线、治理索引、证据、任务和P1材料；
2. `KG_28/docs/P2/`：本轮P2架构、Gate、安全、迁移和实施计划，当前有多份持续新增文档。

风险：代码与P2决策不在同一个Git边界中，未来即使只初始化`KG_20260727`，P2架构文档也不会自动与Commit绑定。

推荐未来权威仓库将代码和批准文档纳入同一个Private Monorepo。P2文档的权威迁入、同步或保留策略必须先人工确认；本评审不移动文件。

## 2.5 Scripts

当前：

- `KG_20260727/scripts/`包含治理索引构建和`validate-governance.py`；
- `backend/scripts/`包含P1 Seed和Backend测试入口；
- Frontend无独立`scripts/`目录；
- KG_28无独立`scripts/`目录。

未来必须区分：

- 可提交的治理/测试/Seed脚本；
- CI入口；
- 破坏性数据库脚本；
- 本地临时脚本。

任何破坏性脚本需要独立审批、目标Preflight和Test Database Isolation保护。

## 2.6 Deploy

在`KG_20260727`和`KG_28`下未发现`deploy/`、`deployment/`、`infra/`或`ops/`项目目录。

这意味着当前没有可追踪的：

- 环境部署定义；
- 镜像版本/Digest；
- Database/Timescale版本声明；
- Secret引用；
- CI/CD部署入口；
- 回滚部署Manifest。

Phase 1是否需要建立`deploy/`必须在后续实施任务中审批。本评审只记录缺口，不创建目录。

## 2.7 推荐未来仓库边界

推荐候选：

```text
Private Repository Root: KG_20260727/

backend/
frontend/
docs/
scripts/
deploy/       # 仅在部署基线审批后建立
README.md
```

理由：现有代码、P1治理文档和脚本已集中于此，范围比`KG_康邻`根目录更小，可避免把产品原型、历史项目、NAS临时文件和无关资产整体提交。

在初始化前必须人工决定如何把`KG_28/docs/P2`的批准文档纳入该仓库。未经批准不得复制或移动。

---

# 3. Commit Traceability Requirement

## 3.1 追踪链

```text
Architecture Decision / Gate
  ↓ authorizes
Task
  ↓ implemented by
Commit(s)
  ↓ validated by
CI Run
  ↓ produces
Test Evidence + Build Artifact + Security Evidence
  ↓ approved through
Review / Pull Request
  ↓ released as
Release Commit / Tag
  ↓ recoverable by
Rollback Target + Runbook
```

任一环节缺失时，Phase 1变更不得宣称“可部署”或“可回滚”。

## 3.2 Task要求

每个Task必须记录：

- 稳定Task ID；
- 目标与明确非目标；
- 关联Architecture Document及Decision/Gate；
- 允许修改路径；
- 禁止范围；
- 成功标准；
- 测试要求；
- Migration/数据库影响；
- 回滚要求；
- Owner和Reviewers。

Decision-003、008、009相关Task必须保持Blocked，不得创建Feature实现任务。

## 3.3 Commit要求

每个Commit至少可追溯到：

- Task ID；
- Base Commit；
- 变更意图；
- 关联Domain；
- 是否涉及Schema/Migration；
- 测试范围；
- 作者与Reviewer；
- 不得包含Secret、构建产物或本地数据库数据。

Commit应保持单一意图，禁止把格式化、重构、依赖升级和业务变更混为一个Commit。

## 3.4 Architecture Document关系

- 文档必须与代码在同一Commit或由明确的前置Commit引用；
- 文档标题、版本和状态可被PR引用；
- Commit不得声称实现未Approved文档；
- 架构范围变化先更新Decision/Gate，再更新代码；
- 文档Hash或Commit SHA形成稳定证据，不使用本地绝对路径作为唯一引用。

## 3.5 Test Evidence关系

每个CI Run必须记录：

- Commit SHA；
- Branch和PR；
- Task ID；
- 运行环境及依赖版本；
- Unit/Integration/Migration测试结果；
- Database镜像、Revision和Run ID；
- Test Database Isolation Preflight结果；
- 失败用例和重跑记录。

本地口头“已测试”不能代替CI Evidence。

## 3.6 Artifact关系

Artifact必须绑定Commit SHA和CI Run ID，至少包括：

- Backend测试报告；
- Frontend Build产物或校验摘要；
- Migration验证报告；
- Schema Manifest/Diff报告；
- 安全扫描和SBOM；
- Test Database生命周期证据；
- Release Manifest；
- 不含Secret和真实健康数据。

## 3.7 Rollback关系

每次Release需要：

- 上一个已批准Release Tag/Commit；
- 应用回滚目标；
- Migration回滚或前向修复策略；
- Mapping/Read Rollback检查点；
- Artifact和镜像Digest；
- 数据备份/恢复点引用；
- 回滚演练CI Run；
- 决策人和停止条件。

Rollback不能只写“回退上一个版本”，必须指向具体Commit/Tag和数据策略。

## 3.8 最小追踪记录

| 对象 | 必须关联 |
|---|---|
| Task | Architecture Doc + Scope + Owner |
| Commit | Task ID + Base SHA + Tests |
| PR | Commits + Review + CI Runs |
| CI Run | Commit SHA + Environment + Evidence |
| Artifact | Commit SHA + Run ID + Hash |
| Release | PR + Tag + Artifact + Approval |
| Rollback | Release + Previous Tag + Data/Schema Strategy |

---

# 4. Branch Strategy

## 4.1 原则

- 仓库必须Private；
- `main`和`develop`受保护；
- 禁止直接Push；
- 所有变更通过Pull Request和CI；
- Branch命名包含Task ID；
- 不在Branch名中包含手机号、客户、Member或健康信息；
- 任何Schema/Migration变更需要数据库Reviewer；
- AI生成变更与人工变更使用相同Gate。

## 4.2 `main`

责任：

- 只保存已发布或可发布基线；
- 每次合并对应Release审批；
- 必须通过全部Required Checks；
- 使用受控Release Tag；
- 禁止Force Push和历史改写；
- 只有批准的Release PR可以进入。

`main`不是日常集成Branch。

## 4.3 `develop`

责任：

- Phase 1日常集成基线；
- Feature Branch从当前批准`develop`创建；
- PR合入前执行Unit、Integration、Migration和安全检查；
- 禁止直接Push和Force Push；
- 每个Sprint Exit形成可识别Commit；
- 不允许Blocked Decision代码进入。

## 4.4 `feature/*`

命名建议：

```text
feature/<task-id>-<short-slug>
```

规则：

- 从`develop`创建；
- 只处理一个Task和一个主要Domain目标；
- 生命周期短；
- 合并前同步最新`develop`并解决冲突；
- 必须有Architecture Doc引用和测试证据；
- 合并后删除远端Feature Branch；
- 禁止在Feature Branch执行Production部署。

## 4.5 `release/*`

命名建议：

```text
release/<version>
```

规则：

- 从已稳定`develop`创建；
- 只允许修复Release阻塞缺陷、文档、版本和部署Manifest；
- 禁止新增业务范围；
- 完成全量P1回归和Phase 1 Gate验证；
- 合入`main`后创建Release Tag；
- 必须把必要修复同步回`develop`；
- Release Branch不绕过UUID、安全、迁移或测试数据库Gate。

## 4.6 合并策略

建议Feature PR使用可追踪的单一合并策略，并保留PR编号和Task ID。具体选择Squash或Merge Commit由团队在初始化前统一冻结；不能不同成员随意切换。

Release PR应保留清晰Release边界。所有策略必须满足：

- Commit SHA稳定；
- PR和Review可查询；
- CI Evidence绑定最终待合并Commit；
- Release Tag指向已验证Commit；
- 回滚目标明确。

---

# 5. CI Capability Review

## 5.1 检查结果

在`KG_20260727`和`KG_28`中排除`.venv`、`node_modules`和缓存后，未发现：

- GitHub Actions；
- GitLab CI；
- Jenkinsfile；
- Azure Pipelines；
- Bitbucket Pipelines；
- CircleCI；
- Buildkite。

Frontend `node_modules`中存在第三方包自带CI文件，但不属于本项目。

## 5.2 当前可用本地入口

- Backend有`backend/scripts/test-backend.ps1`；
- 根`scripts/validate-governance.py`可验证部分治理基线；
- Frontend有`npm run build`；
- Backend有pytest/Unittest测试资产；
- Integration Test有Alembic和真实PostgreSQL Fixture。

这些是CI候选入口，不等于已经存在CI。

## 5.3 当前缺口

- 无PR触发器；
- 无Branch Protection Required Check；
- 无依赖缓存与锁定验证；
- 无PostgreSQL/Timescale一次性服务；
- 无Test Database Isolation Preflight；
- 无Migration lifecycle Job；
- 无Artifact归档；
- 无SBOM/Secret/Dependency扫描；
- 无Release和Rollback Manifest；
- 无CI Secret与Environment审批证据。

## 5.4 平台选择边界

若未来Private仓库托管在GitHub，可优先评估GitHub Actions，以减少Remote和CI权限系统分离。但平台选择仍需人工审批，本文件不创建GitHub仓库或Workflow。

无论选择何种CI，必须满足第6节能力，不允许因平台差异降低测试数据库隔离或审计要求。

---

# 6. Phase 1 CI Requirement

## 6.1 必需Pipeline

```text
Source / Governance Check
  ↓
Backend Unit Test
  ↓
Frontend Build Check
  ↓
Test Database Isolation Preflight
  ↓
Integration Test
  ↓
Migration Test
  ↓
Security / Secret / Dependency Check
  ↓
Artifact Collection
  ↓
Gate Summary
```

## 6.2 Unit Test

必须：

- 使用固定Python/Node版本；
- 执行Backend Unit/API/Repository测试；
- 执行治理校验；
- 执行Frontend TypeScript Build；
- 输出机器可读测试报告；
- 失败时阻止合并；
- 不连接UAT/Production；
- 不产生真实外部消息、支付或AI调用。

## 6.3 Integration Test

必须：

- 使用`P2_TEST_DATABASE_ISOLATION_PLAN_V1.md`定义的一次性实例；
- 固定PostgreSQL 16、TimescaleDB版本和镜像Digest；
- 每个Run使用独立Database、Credential、Run ID和TTL；
- UAT URL负向保护通过；
- 执行真实SQLAlchemy/asyncpg往返；
- 执行Tenant/Store/Member隔离负向测试；
- 失败或成功后都销毁资源；
- 不能访问UAT、Staging或Production网络。

## 6.4 Migration Test

必须：

- Empty Database → Head；
- P1 `20260728_0006` → Phase 1 Candidate；
- Stepwise Upgrade；
- 批准的Downgrade/Forward Fix；
- Re-upgrade；
- 重复执行和幂等；
- P1 Schema/数据不破坏验证；
- Health Indicator/Timescale完整性；
- Decision-003/008/009对象不存在验证；
- 只在一次性Migration Test Database运行。

## 6.5 Artifact保存

必须保存并绑定Commit SHA与Run ID：

| Artifact | 内容 | 敏感限制 |
|---|---|---|
| Unit Report | 用例、结果、耗时 | 不含Token/Secret |
| Integration Report | DB版本、Revision、结果 | 不含完整URL/密码 |
| Migration Report | 起止Revision、Schema摘要、回退 | 不含真实健康数据 |
| Isolation Evidence | Preflight、Disposable ID、销毁结果 | 指纹脱敏 |
| Build Artifact | Frontend/Backend可发布制品或Hash | 不含`.env` |
| Security Evidence | Secret/Dependency扫描、SBOM | 安全报告限制访问 |
| Release Manifest | Commit、Tag、Artifact Hash | 不含凭据 |
| Rollback Evidence | 上一版本、演练结果 | 不含备份密钥 |

Artifact保留期、访问角色和销毁策略需由安全/合规批准。

## 6.6 CI触发与Gate

| 事件 | 最低检查 |
|---|---|
| Feature PR → develop | Governance、Unit、Build、受影响Integration/Migration、安全扫描 |
| Merge develop | 全量Unit、Integration、Migration、Artifact |
| Release PR → main | 全量P1回归、Phase 1验证、安全、SBOM、Rollback演练 |
| Tag/Release | 验证Tag指向已通过Gate Commit，生成Release Manifest |

禁止仅因“文档变更”自动跳过治理和Secret扫描；测试范围可按路径优化，但Required Gate不能消失。

## 6.7 CI Secret和Private仓库

- 仓库Visibility必须为Private；
- CI使用受控Environment Secret，不使用个人密码；
- Production/UAT Secret不能暴露给普通PR；
- 外部Fork PR不得获得敏感Secret；
- Job权限默认只读，按需提升；
- Token短期、最小权限、可轮换；
- Log自动脱敏并禁止输出数据库URL；
- 私有仓库成员、Admin和CI App权限需定期审计。

---

# 7. Codex Development Traceability

## 7.1 原则

AI辅助修改与人工修改遵守同一Branch、Commit、Review、CI和Rollback规则。AI不是审批人，也不能自行扩大Phase 1范围。

## 7.2 必须绑定

每次Codex开发必须绑定：

```text
Codex Task / User Request
  ↓
Architecture Gate + Allowed Paths
  ↓
Base Commit SHA
  ↓
Feature Branch
  ↓
Changed Files / Diff
  ↓
Test Commands + CI Run
  ↓
Human Review / Approval
  ↓
Final Commit / PR
```

## 7.3 Codex Task记录

至少包括：

- Task ID和标题；
- 用户原始目标摘要；
- Base Commit SHA；
- 工作Branch；
- 允许/禁止范围；
- 使用的架构文档版本；
- 关键假设和风险；
- 修改文件；
- 实际执行测试；
- 未执行测试及原因；
- 数据库/外部系统是否接触；
- 最终Commit/PR和Reviewer。

禁止把用户密码、Token、数据库Secret或健康数据写入Task记录。

## 7.4 AI Commit规则

- Commit Message包含Task ID，不包含Prompt全文或敏感数据；
- AI修改必须由人类Reviewer批准；
- 不能由同一自动身份同时生成、批准和发布；
- 未执行测试必须明确标记，不能写成通过；
- AI发现范围外问题只记录，不顺手修复；
- 任何Migration或安全边界变更需要专门Reviewer；
- Decision-003、008、009继续Block，AI不得创建占位实现规避Gate。

## 7.5 Review要求

Reviewer必须确认：

- Diff与Task一致；
- 没有无关重构；
- 架构文档和Decision状态正确；
- P1历史语义不变；
- Test Evidence对应最终Commit；
- Secret扫描通过；
- Rollback目标明确；
- Artifact来自同一Commit；
- AI说明与实际Diff一致。

## 7.6 Codex Protocol关系

用户声明`P2_CODEX_DEVELOPMENT_PROTOCOL_V1.md`已完成，但前序工作区检查未找到该文件。进入实施前，必须找到或恢复其批准版本，并与本节追踪要求比对；如有冲突，以人工批准的最新Gate为准并记录Decision。

---

# 8. Readiness Gate

## 8.1 当前判定

```text
P2 Git CI Baseline Readiness:
NOT READY

P2 Git CI Baseline Review:
REVIEW READY

P2 Phase 1 Implementation:
NOT OPEN
```

## 8.2 NOT READY原因

1. 所有候选目录均不是Git工作树；
2. 无Branch、HEAD Commit、Status或Remote；
3. 无证据证明GitHub Private仓库存在；
4. 无Branch Protection和PR Review；
5. 无项目级CI；
6. 无Commit → Run → Artifact → Rollback追踪；
7. 代码与P2文档位于不同目录主线；
8. 无deploy/infra基线；
9. Test Database Isolation尚未实施；
10. Codex Development Protocol文件位置未确认；
11. 用户提供的密码已暴露，必须先轮换且不得用于自动化。

## 8.3 转为READY WITH CONDITIONS的最低条件

- [ ] 人工批准权威仓库Root和P2文档纳入策略
- [ ] 用户轮换已暴露密码
- [ ] 在`huberyxyh`或批准组织下创建Private仓库
- [ ] 验证Visibility为Private
- [ ] 初始化Git历史并建立可信Initial Baseline Review
- [ ] 配置不含Credential的Remote
- [ ] 建立`main`和`develop`及保护规则
- [ ] 建立Feature PR和Human Review流程
- [ ] 建立最小CI：Governance、Unit、Frontend Build、Secret Scan
- [ ] 建立Commit/Task/PR/Run/Artifact标识规范
- [ ] 找到并批准Codex Development Protocol

## 8.4 转为READY的完整条件

在最低条件基础上：

- [ ] Test Database Isolation实施并通过UAT URL负向测试
- [ ] Integration Test使用独立Ephemeral Timescale实例
- [ ] Migration Test覆盖Empty/P1 Head/Upgrade/Rollback/Re-upgrade
- [ ] CI保存脱敏Artifact并绑定Commit SHA/Run ID
- [ ] UUID Validation、SBOM和Dependency Scan进入CI
- [ ] Release Branch、Tag和Release Manifest流程通过演练
- [ ] Application、Migration、Mapping和Read Rollback证据绑定Release
- [ ] P1完整回归在最终候选Commit通过
- [ ] 技术、测试、安全、运维和P1维护负责人签批

## 8.5 私有仓库强制规则

```text
Repository Visibility:
PRIVATE — MANDATORY

Public Repository:
PROHIBITED

Password in Git / Remote / CI:
PROHIBITED
```

若误创建Public仓库，立即停止Push、撤销凭据、删除已暴露Remote内容并启动Secret/数据泄露评估；不能仅把Visibility改回Private后继续开发而不审计暴露范围。

## 8.6 Pending Decision

```text
Decision-003:
BLOCKED

Decision-008:
BLOCKED

Decision-009:
BLOCKED
```

Git/CI建立不会改变任何Domain Decision状态。

## 8.7 最终检查

- [x] 只读检查Git、目录和CI状态
- [x] 未修改Git
- [x] 未初始化仓库
- [x] 未创建Branch
- [x] 未创建Remote或GitHub仓库
- [x] 未登录GitHub
- [x] 未使用或记录用户密码
- [x] 未修改CI
- [x] 未修改代码
- [x] 未修改配置
- [x] 未修改数据库
- [x] 仅新增本评审Markdown文档

**最终结论：Git客户端与代码结构具备建立基线的条件，但当前没有Git仓库、Remote或CI，无法满足Phase 1的Commit、Review、Artifact和Rollback追踪要求。状态为`NOT READY`；完成Private仓库、分支保护、CI、Test Database Isolation和追踪链后重新评审。**
