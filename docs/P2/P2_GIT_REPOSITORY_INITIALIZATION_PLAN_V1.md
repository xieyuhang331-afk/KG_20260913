# P2 Git Repository Initialization Plan V1

> 项目：KG-康邻智慧健康平台  
> 阶段：P2 Phase 1 — Private Monorepo Initialization Planning  
> 上游依据：`P2_GIT_CI_BASELINE_REVIEW_V1.md`  
> 目标托管Owner：`huberyxyh`或后续批准的组织账号  
> 仓库可见性：**Private Only**  
> 文档状态：**REVIEW READY**  
> 本文性质：初始化方案，不是Git或GitHub执行授权

## Status Summary

```text
P2 Git Repository Initialization Plan:
REVIEW READY

P2 Git Initialization:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

本方案推荐以`KG_20260727`为未来私有Monorepo Root。初始化前必须解决Synology ReparsePoint文件物化、P2文档权威迁入、Secret扫描、根级忽略规则以及外部PRD/原型治理依赖等问题。本次不执行`git init`、不创建GitHub仓库、Remote或Branch，不移动文件。

---

# 1. Repository Root Decision

## 1.1 候选A：`KG_20260727`

路径：

```text
E:/nas/JR/SynologyDrive/KG_康邻/研发/平台/KG_20260727
```

### 优势

- 已包含`backend`、`frontend`、`docs`、`scripts`和根README；
- 与当前P1代码主线一致；
- 范围足以形成应用Monorepo；
- 不会自动纳入全部产品资料、历史工程和NAS其他业务文件；
- 现有README已经把该目录定义为2026-07-27后的项目主线；
- Backend/Frontend可共享同一Commit、PR、CI Run和Release Tag；
- 可在后续审批后纳入P2文档及部署基线。

### 风险

- 当前不是Git工作树；
- 根目录没有`.gitignore`；
- 包含`.venv`、`node_modules`、`dist`、缓存和egg-info等本地产物；
- P2架构文档当前位于`KG_28/docs/P2`，不在候选Root内；
- 大量文件具有Synology`ReparsePoint`属性，必须验证物化与Git行为；
- 治理脚本依赖Root之外的`Tech/KG_project`资料，干净Clone中可能无法运行；
- 当前没有`deploy`目录和CI。

### 判断

**Recommended，前提是完成Pre-initialization Gate。**

## 1.2 候选B：`KG_康邻`

路径：

```text
E:/nas/JR/SynologyDrive/KG_康邻
```

### 优势

- 能覆盖研发、产品和其他关联目录；
- 外部资料路径相对更接近统一工作区；
- 不需要立即决定KG_28与KG_20260727的文档归属。

### 风险

- 范围过大，可能包含产品原型、历史工程、附件、敏感业务资料和无关文件；
- Secret、大文件、二进制和个人工作文件暴露面显著增加；
- 初始Commit不可审查；
- Code Owner、权限和Release边界模糊；
- CI触发范围过宽；
- 产品/历史资料与可发布应用代码生命周期不同；
- 即使Private，也不应把所有NAS内容纳入同一个Git仓库。

### 判断

**Rejected for Phase 1 Monorepo。**

## 1.3 候选C：新建其他目录

例如新建`KG_platform_monorepo`。

### 优势

- 可以从干净目录开始；
- 能预先设计标准结构；
- 不受现有本地产物干扰。

### 风险

- 需要复制/移动代码和文档；
- 容易形成第三条项目主线；
- 现有绝对/相对路径和治理索引需要重新验证；
- P1 UAT代码与新目录可能漂移；
- 不符合“基于现有架构、避免重新设计”的原则。

### 判断

**Not Recommended unless ReparsePoint或同步机制证明原目录不适合Git。**

## 1.4 最终推荐

```text
Recommended Repository Root:
E:/nas/JR/SynologyDrive/KG_康邻/研发/平台/KG_20260727

Repository Model:
Private Monorepo

Repository Visibility:
Private — Mandatory
```

## 1.5 Root冻结前的Preflight

- [ ] 确认`KG_20260727`是唯一P1/P2代码主线
- [ ] 确认Synology ReparsePoint文件全部本地物化且可稳定读取
- [ ] 在隔离副本上验证Git跟踪、Clone和Hash一致性
- [ ] 盘点所有待跟踪文件和排除文件
- [ ] 完成Secret、大文件、数据库Dump和个人数据扫描
- [ ] 确认P2文档纳入策略
- [ ] 处理外部`Tech/KG_project`治理依赖的CI可用性
- [ ] 由产品、技术、安全和P1维护负责人批准Root

---

# 2. Monorepo Structure

## 2.1 目标结构

```text
KG_20260727/
├─ backend/
│  ├─ app/
│  ├─ tests/
│  ├─ scripts/
│  ├─ pyproject.toml
│  └─ alembic.ini
├─ frontend/
│  ├─ src/
│  ├─ package.json
│  └─ package-lock.json
├─ docs/
│  ├─ governance/
│  ├─ evidence/
│  ├─ handoffs/
│  ├─ P1/
│  └─ P2/
├─ scripts/
├─ deploy/                    # 仅在部署基线获批后创建
├─ README.md
├─ .gitignore                 # 未来实施对象
└─ CI workflow files          # 仅在CI方案获批后创建
```

该结构是未来目标，不授权本次创建、移动或重命名。

## 2.2 纳入范围

未来应跟踪：

- Backend源码、测试、Migration历史和受控脚本；
- Backend依赖声明；
- Frontend源码、配置、依赖声明及锁文件；
- 项目章程、治理索引、架构文档、P1/P2 Gate和Evidence；
- 可复现的治理/构建/测试脚本；
- 获批后建立的部署Manifest和CI Workflow；
- README、License/Notice及安全政策（如批准）。

## 2.3 排除范围

未来不得跟踪：

- `.venv/`；
- `node_modules/`；
- `dist/`和其他构建输出；
- `.pytest_cache/`、`__pycache__/`、`*.pyc`；
- `*.egg-info/`；
- `.env`及环境Secret；
- 数据库Dump、Backup、SQLite运行文件；
- 本地日志、覆盖率临时文件和测试临时数据；
- IDE/OS个人文件；
- UAT截图或Artifact，除非进入专门审批的Evidence策略；
- 产品敏感源文件，除非明确批准纳入Private仓库。

当前仅`.venv`约107MB、`node_modules`约125MB，必须在任何初始Stage前排除。

## 2.4 Domain边界

Monorepo不等于Domain耦合：

- Backend保持模块化单体；
- Frontend保持独立构建；
- P1兼容模块与P2 Foundation模块边界不变；
- Docs作为决策与证据，不直接被运行时代码修改；
- Deploy和CI不能绕过Database/UUID/Security/Migration Gate；
- Decision-003、008、009继续Blocked。

## 2.5 外部来源依赖

现有治理脚本和`source-baseline.yaml`依赖仓库Root之外的：

```text
Tech/KG_project/康邻健康管理平台726/
```

干净Clone中该路径不会自然存在。初始化前必须从以下方案中人工批准一个：

1. 将允许分发的来源快照以受控方式纳入Private仓库；
2. 将来源放入独立Private资料仓库并以固定Commit/Artifact引用；
3. CI从受控Artifact Store获取只读快照；
4. 将治理校验拆分为“仓库内可运行”和“受控来源环境运行”两级。

禁止在CI中依赖某台NAS的未版本化绝对路径，也禁止未经授权把完整产品资料提交到GitHub。

---

# 3. P2 Docs Migration Strategy

## 3.1 当前状态

P2文档当前位于：

```text
E:/nas/JR/SynologyDrive/KG_康邻/研发/平台/KG_28/docs/P2
```

代码主线位于`KG_20260727`。如果不处理，Commit不能同时证明“代码实现了哪一版P2决策”。

## 3.2 目标位置

推荐未来权威位置：

```text
KG_20260727/docs/P2/
```

## 3.3 迁移原则

- 先盘点，后迁入；
- 先复制验证，后冻结权威源；
- 不直接覆盖同名文件；
- 文档内容、文件名、状态和Hash必须可核对；
- 迁移过程本身有Task、Commit、Review和Evidence；
- 原KG_28文档在权威切换前保持只读；
- 权威切换后禁止双向编辑；
- 不因迁移改变Decision状态或正文含义；
- 不把未批准草稿伪装成Approved。

## 3.4 未来迁移阶段

### Phase D0：Inventory

- 生成文件清单、大小、修改时间和Hash；
- 标记Draft/Review Ready/Frozen/Approved；
- 标记重复、缺失引用和命名冲突；
- 确认当前P2文档总量和Owner。

### Phase D1：Target Preparation

- 在未来仓库结构中准备`docs/P2`；
- 定义文档索引、版本和链接规则；
- 确认绝对路径引用如何改为仓库相对引用；
- 不修改业务Decision。

### Phase D2：Controlled Import

- 按批准清单复制文档；
- 保持内容与Hash；
- 对确需路径调整的文件单独Review；
- 使用独立Docs Import Commit；
- CI校验链接、状态和重复文件。

### Phase D3：Authority Switch

- 人工确认`KG_20260727/docs/P2`为唯一权威；
- KG_28副本转为只读Archive或指向权威位置；
- 更新文档入口和Handoff；
- 禁止继续在两处分别修改。

### Phase D4：Verification

- Clone后文档完整；
- 所有上游/下游引用可解析；
- Commit SHA可关联代码Task；
- 原始Hash和迁入Hash有证据；
- 无Secret或敏感附件误入。

## 3.5 本次限制

本次不创建`KG_20260727/docs/P2`，不复制、不移动、不删除KG_28文件。

---

# 4. .gitignore Strategy

## 4.1 当前状态

- `KG_20260727`根目录无`.gitignore`；
- Backend `.gitignore`覆盖Python缓存、`.venv`和`.env`；
- Frontend `.gitignore`覆盖`node_modules`、`dist`和TypeScript Build信息；
- 子目录规则不足以覆盖Monorepo根级Artifact、Secret、IDE和数据库文件。

## 4.2 根级策略

未来根`.gitignore`应作为安全Gate，在`git add`之前完成并Review。规则类别：

### Python

- 虚拟环境；
- `__pycache__`和字节码；
- pytest/mypy/ruff/coverage缓存；
- egg/build/wheel产物；
- 本地测试输出。

### Node/Vite

- `node_modules`；
- `dist`、coverage和缓存；
- 本地Vite环境文件；
- TypeScript Build Info。

### Secret与配置

- `.env`及所有环境变体，保留经过审查的`.env.example`例外；
- PEM/KEY/PFX/P12；
- SSH Key；
- Credential、Token和Secret文件；
- 本地数据库URL配置；
- Cloud/CI本地认证缓存。

### 数据与数据库

- SQL Dump、Backup、SQLite运行文件；
- 测试数据库数据目录；
- Timescale/PostgreSQL本地Volume；
- Seed导出和临时校验文件；
- 含真实健康数据的任何文件。

### IDE/OS/同步产物

- `.idea`、个人VS Code设置（团队批准配置除外）；
- Windows/macOS临时文件；
- Synology冲突副本和同步临时文件；
- 日志与Crash Dump。

## 4.3 Ignore不是安全边界

- `.gitignore`不能阻止已Stage文件；
- 初始化前必须先生成候选文件清单再Stage；
- 使用Allowlist式Initial Commit Review；
- Secret扫描必须在Stage前和Commit后各执行；
- 大文件和二进制需独立审批；
- 忽略规则不得隐藏应版本化的Migration、测试或架构文档。

## 4.4 ReparsePoint策略

当前大量文件带Synology`ReparsePoint`属性但没有普通符号链接Target。初始化前必须在隔离副本验证：

- 文件内容已本地物化；
- Git读取的是文件内容而非不可移植占位符；
- Clone后Hash与源一致；
- 长路径、中文文件名和换行符保持稳定；
- 同步冲突不会产生未审查副本；
- Git操作期间Synology不会部分下载或替换文件。

验证失败时才重新评估候选C新目录，不得直接在原目录初始化。

---

# 5. Secret Protection Strategy

## 5.1 原则

- Private仓库仍按可能泄露处理Secret；
- 不提交任何密码、Token、数据库URL、私钥或真实健康数据；
- 对话、工单、文档和Commit Message同样不能保存Secret；
- 已暴露Credential先轮换，再初始化仓库；
- Git历史中的Secret不能仅靠后续删除文件修复。

## 5.2 初始化前扫描

必须覆盖：

- Backend/Frontend配置与源码；
- Docs、Evidence、Handoff和历史Task；
- Shell/PowerShell/Python脚本；
- Migration和Seed；
- `.env`及未被忽略的变体；
- 证书、Key、Dump、Backup和日志；
- GitHub/Cloud/Database/API Token模式；
- 手机号、证件号和真实健康数据样本；
- 大文件和二进制附件。

扫描结果需分类为Secret、PII/PHI、合法测试样例、误报，并由安全负责人批准。

## 5.3 Credential规则

- 用户已在对话中提供的密码必须轮换；
- 不用个人密码做Git Remote或CI认证；
- Future Remote使用SSH Key、短期Token或批准的GitHub App；
- Token最小权限且只允许Private目标仓库；
- CI Secret按Environment隔离；
- UAT/Production Secret不向Feature PR暴露；
- Secret不出现在URL、Command History、日志和Artifact中。

## 5.4 GitHub Private要求

- 创建时Visibility显式为Private；
- 创建后由第二位Reviewer确认Private；
- 禁止启用Public Pages或匿名Artifact；
- Collaborator使用最小权限；
- Admin数量最小化；
- Branch Protection、Secret Scanning、Dependabot/Dependency Review能力按可用性审批；
- Repository Transfer、Visibility Change和Archive均需双人审批；
- 误公开时立即停止Push、撤销Credential并启动暴露评估。

## 5.5 Commit前保护

- Root ignore规则通过Review；
- 候选Stage清单通过人工审阅；
- Secret扫描为零未处置高风险；
- 禁止Stage `.venv`、`node_modules`、`dist`或Dump；
- 不允许`git add .`作为未经清单审查的初始化方式；
- Initial Commit生成后再次扫描整个历史和Tree。

---

# 6. Initial Commit Strategy

## 6.1 目标

Initial Baseline必须能回答：

- P1 UAT通过的是哪套代码；
- 当前Alembic Head是什么；
- 哪些文档构成架构基线；
- 哪些本地产物被排除；
- 哪些已知风险和Blocked Decision存在；
- 后续任何变更相对于哪个可信SHA。

## 6.2 初始化前冻结窗口

未来执行时应短时冻结源目录写入：

- 停止代码和文档并发修改；
- 不停止P1运行服务，但冻结文件变更；
- 记录开始/结束时间和参与人；
- 生成源文件Manifest与Hash；
- 确认Synology同步完成；
- 发现冲突副本即停止初始化。

## 6.3 推荐Commit序列

### Commit 0：Repository Policy Baseline

未来内容候选：

- 根README/Repository Scope；
- `.gitignore`；
- Security/Contribution/Branch规则；
- 不包含业务代码变更。

### Commit 1：P1 Code Baseline

- Backend源码、测试、Migration和受控脚本；
- Frontend源码与依赖锁；
- `KG_20260727/docs`现有治理基线；
- 根脚本；
- 对应P1 Revision和UAT证据Manifest。

### Commit 2：P2 Architecture Docs Import

- 仅在P2 Docs Migration审批后；
- 按批准Manifest导入`docs/P2`；
- 不混入代码变更；
- 记录源Hash和权威切换。

### Commit 3：CI Baseline

- 仅在CI设计和Test Database Isolation实施获批后；
- Workflow、Artifact和Branch Required Checks；
- 不同时引入Phase 1 Domain代码。

该序列使P1代码、P2决策和CI基线可独立Review。具体Commit数量可在执行审批时调整，但不能把未审查的全部NAS内容作为一个Initial Commit。

## 6.4 Initial Baseline Evidence

- 文件Manifest及Hash；
- Secret/PII/PHI扫描报告；
- 排除文件统计；
- Python/Node/PostgreSQL/Timescale版本；
- Alembic Head `20260728_0006`；
- Backend测试和Frontend Build结果；
- Platform/Institution UAT报告引用；
- 已知风险清单；
- Decision-003/008/009 Blocked声明；
- Human Review和批准记录。

## 6.5 Initial Commit验收

- 干净Clone成功；
- 文件Hash一致；
- 中文路径和换行符正确；
- `.venv/node_modules/dist/cache`未进入Tree；
- 无Secret和真实健康数据；
- P1构建/测试可按Runbook执行；
- 文档链接有效；
- Git状态干净；
- Commit SHA归档为R0 Baseline。

---

# 7. Branch Strategy

## 7.1 `main`

- Private仓库默认/发布Branch；
- 只保存已批准Release；
- 禁止直接Push和Force Push；
- PR、Required CI、Human Review必需；
- Release Tag只能指向通过Gate的Commit；
- 只允许`release/*`按批准流程合入。

## 7.2 `develop`

- Phase 1日常集成Branch；
- 从Initial Baseline建立；
- 禁止直接Push和Force Push；
- Feature PR合入目标；
- Unit、Integration、Migration和安全检查为Required；
- 每个Sprint Exit形成可识别Commit和Evidence。

## 7.3 `feature/*`

格式：

```text
feature/<task-id>-<short-slug>
```

- 从`develop`创建；
- 一个Branch只承载一个Task/主要Domain目标；
- 必须引用Architecture Document；
- 不含Decision-003/008/009；
- 合并后删除；
- 不执行Production部署。

## 7.4 `release/*`

格式：

```text
release/<version>
```

- 从稳定`develop`创建；
- 只做Release阻塞修复、版本、文档和Manifest；
- 禁止新增业务范围；
- 全量P1回归、Phase 1验证和Rollback演练通过后合入`main`；
- 必要修复同步回`develop`。

## 7.5 初始化顺序边界

未来执行顺序建议：

1. Initial Baseline Commit落在`main`；
2. 配置`main`保护；
3. 从已批准`main`建立`develop`；
4. 配置`develop`保护；
5. CI Required Checks就绪；
6. 才允许创建第一个`feature/*`。

本次不创建任何Branch。

---

# 8. Remote Repository Requirements

## 8.1 GitHub仓库要求

```text
Owner:
huberyxyh or an approved organization account

Visibility:
Private

Public Visibility:
Prohibited
```

仓库名称由人工审批，应清晰表达项目且不包含个人信息、Secret或环境名称。

## 8.2 创建审批

创建前必须确认：

- Owner账号Credential已安全轮换；
- 是否应使用个人Owner或组织Owner；
- Admin、Maintainer、Developer和Read角色；
- 仓库Visibility为Private；
- 数据存储地域、合规和公司政策；
- 产品文档和健康平台资料是否允许托管；
- 备份、Transfer和账号离职接管方案。

对于团队/商业项目，组织Owner通常比个人账号更利于权限和交接；最终由负责人决定。

## 8.3 Remote规则

- Remote名称使用`origin`；
- URL不含用户名密码或Token；
- 优先使用批准的SSH/HTTPS凭据管理；
- 第一次Push前再次确认目标Owner、仓库名和Private状态；
- 推送后由第二人验证Remote Tree和Visibility；
- 禁止同时配置未经批准的个人镜像Remote；
- 禁止向Public Fork推送。

## 8.4 Branch Protection

`main`和`develop`至少要求：

- PR合并；
- 至少一名人类Reviewer；
- Required CI通过；
- 禁止Force Push；
- 禁止删除受保护Branch；
- Review失效规则覆盖新Commit；
- 管理员绕过受审计并最小化；
- Schema/Migration变更要求数据库Code Owner；
- 安全配置变更要求安全Reviewer。

## 8.5 Remote恢复

- 记录Repository ID、Owner和创建审批；
- Release Tag和关键Branch有备份策略；
- 定期验证Clone和恢复；
- 账号不可用时有组织级接管路径；
- Remote误配时停止Push并验证是否产生数据暴露。

---

# 9. CI Integration Preparation

## 9.1 初始化前准备

- 冻结CI平台选择；
- 确认Private仓库功能与权限；
- 定义Python/Node版本；
- 锁定PostgreSQL/Timescale镜像；
- 完成Test Database Isolation实施设计审批；
- 定义Artifact格式、保留期和访问角色；
- 定义Secret/Dependency/SBOM扫描；
- 解决外部治理来源在CI中的可用性；
- 建立CI失败和Cleanup失败告警。

## 9.2 最小CI阶段

### CI-0：Repository Safety

- Secret扫描；
- 大文件/禁止路径检查；
- P2 Decision Blocked扫描；
- 文档状态和链接检查。

### CI-1：Build与Unit

- Backend Unit；
- Governance Validation；
- Frontend TypeScript/Vite Build；
- 机器可读报告。

### CI-2：Integration

- Ephemeral PostgreSQL 16 + TimescaleDB；
- UAT URL负向保护；
- SQLAlchemy/asyncpg真实往返；
- Tenant/Store/Member隔离测试。

### CI-3：Migration

- Empty → Head；
- P1 Head → Candidate；
- Upgrade/Downgrade/Re-upgrade；
- Timescale和P1兼容验证。

### CI-4：Artifact与Gate

- 报告、Schema Manifest、SBOM、Hash；
- Commit/Task/Run绑定；
- Cleanup和销毁证明；
- Gate Summary。

## 9.3 CI建立顺序

CI应在Initial Code Baseline后、Phase 1业务开发前建立。CI Workflow本身通过Feature PR和Review进入，不能作为未审查的Initial Commit副作用。

## 9.4 CI未就绪时

- 不允许Phase 1正式实现；
- 可继续文档、测试设计和隔离环境准备；
- 不用本地口头测试替代Required CI；
- 不运行会重置共享数据库的Integration Fixture。

---

# 10. Rollback Strategy

## 10.1 初始化前回滚

初始化执行前创建只读文件Manifest和Hash，不移动源文件。若预检失败：

- 不执行`git init`；
- 不创建Remote；
- 不改变源目录；
- 修复Secret、ReparsePoint或范围问题后重新评审。

## 10.2 本地初始化回滚

未来若本地初始化完成但尚未Push且发现范围错误：

- 停止所有Git操作；
- 保留审计记录和Manifest；
- 由人工确认是否移除本地Git元数据并重新初始化；
- 不删除或重写源代码和文档；
- 不使用强制历史改写掩盖Secret问题。

具体删除动作属于未来破坏性操作，必须单独审批，本计划不授权执行。

## 10.3 Remote创建回滚

若Remote创建错误：

- 在首次Push前停止并删除/归档错误Remote资源；
- 若误设为Public，立即撤销Credential并启动暴露评估；
- 若已Push Secret，按Secret Incident处理，轮换Credential并审查历史；
- 仅改为Private不能替代暴露审计；
- 正确Remote建立后重新验证Owner、Visibility和Branch Protection。

## 10.4 Initial Commit回滚

若Initial Commit包含不应跟踪文件：

- 在未Push前停止；
- 重新生成干净候选Tree和扫描证据；
- 若已Push，评估历史清理、Credential轮换和Artifact删除；
- 保留Incident记录，不用普通删除Commit掩盖历史；
- 所有Reviewer重新批准最终Baseline SHA。

## 10.5 P2 Docs迁移回滚

- 权威切换前：KG_28继续为Source of Truth，可停止Import；
- 权威切换后发现问题：冻结两处编辑，按Manifest恢复已批准文档版本；
- 不通过删除历史Commit消除差异；
- Hash、状态或链接不一致时停止代码实施；
- 恢复后重新批准Authority Switch。

## 10.6 CI回滚

- CI配置异常时停止合并，不绕过Required Check；
- 回退到上一批准Workflow Commit；
- Integration环境异常时禁用破坏性Job，不改用UAT；
- Artifact或Secret泄露时撤销访问并启动安全响应；
- CI恢复后重新运行最终Commit的全部Required Checks。

## 10.7 Initialization Gate

执行任何Git初始化前必须全部满足：

- [ ] Repository Root获人工批准
- [ ] ReparsePoint/物化/Clone验证通过
- [ ] Root `.gitignore`策略获批准
- [ ] Secret/PII/PHI/大文件扫描通过
- [ ] 已暴露Credential完成轮换
- [ ] Initial Commit文件Allowlist获批准
- [ ] P2 Docs迁移策略获批准
- [ ] 外部治理来源CI策略获批准
- [ ] GitHub Owner和Private Visibility获批准
- [ ] Branch和Review策略获批准
- [ ] CI/Test Database Isolation准备方案获批准
- [ ] Rollback和Incident Runbook获批准

## 10.8 最终状态

```text
P2 Git Repository Initialization Plan:
REVIEW READY

Recommended Repository Root:
KG_20260727

Repository Visibility:
PRIVATE — MANDATORY

P2 Git Initialization:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

## 10.9 最终检查

- [x] 只生成初始化设计文档
- [x] 未执行`git init`
- [x] 未创建GitHub仓库
- [x] 未创建Remote
- [x] 未创建Branch
- [x] 未创建Commit
- [x] 未修改Git配置
- [x] 未修改代码
- [x] 未修改CI
- [x] 未修改配置
- [x] 未移动或复制文件
- [x] 未修改数据库
- [x] 未使用或记录用户密码
- [x] 未开放Decision-003/008/009

**当前动作：等待技术、安全、运维、产品文档Owner和P1维护负责人审批Root、文档迁入、Secret保护、Branch、Remote及CI准备方案。在Initialization Gate全部通过前，P2 Git Initialization与Phase 1 Implementation保持`NOT OPEN`。**
