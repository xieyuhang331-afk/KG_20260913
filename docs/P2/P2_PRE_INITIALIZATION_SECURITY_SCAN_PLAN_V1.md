# P2 Pre-Initialization Security Scan Plan V1

> 项目：KG_康邻智慧健康平台  
> 阶段：P2 Phase 1 — Initial Commit 前安全审查  
> Repository Root：`E:/nas/JR/SynologyDrive/KG_康邻/研发/平台/KG_20260727`  
> 文档性质：安全扫描 Gate 方案，不构成扫描或 Git Commit 授权

## Current Status

```text
P2 Git Repository Initialization:
LOCAL BASELINE PREPARED

Branch:
main

Remote:
NOT CONFIGURED

P2 Git Initial Commit:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

当前已经完成本地 Git 初始化并建立根级 `.gitignore`，但尚未证明候选文件不含 Secret、PII/PHI、大文件或数据库产物。`.gitignore` 只能降低误纳入概率，不能替代扫描、人工复核和 Commit Allowlist。因此，在本计划定义的 Gate 获得批准前，不允许执行首次 `git add` 或 `git commit`。

---

# 1. Security Scan Objective

首次 Commit 前安全扫描的目标是建立一个可审计、可复现、无敏感数据污染的 Git 基线，使后续 Commit、CI Run、测试证据与回滚点都能建立在可信起点上。

具体目标：

1. 确认候选代码、文档、脚本和配置中不存在可用或历史凭据；
2. 确认不存在未经授权的真实个人身份信息、客户资料或健康数据；
3. 排除依赖目录、构建产物、数据库文件、备份、日志和不适合 Git 的大文件；
4. 确认 Synology 文件已完整物化，扫描内容与未来 Git 读取内容一致；
5. 建立 Commit 0、Commit 1 的显式 Allowlist；
6. 形成与候选文件 Manifest 绑定的扫描报告、Hash 和人工审批记录；
7. 发现风险时停止初始化流程，先完成凭据轮换、数据移除或范围调整，再重新扫描。

本 Gate 不是代码质量、业务验收或依赖漏洞扫描的替代品，也不授权 P2 Phase 1 开发。

## 1.1 放行原则

- Private Repository 仍按可能泄露的环境处理；
- “没有被 `.gitignore` 排除”不等于允许提交；
- “测试数据”“UAT 数据”“内部文档”不自动豁免；
- 扫描结果必须绑定同一个候选 Manifest Hash；
- 扫描后文件发生变化，受影响范围必须重新扫描；
- 未处置的 Critical 或 High Finding 必须为零；
- 无法判断是否敏感时，按敏感内容处理。

---

# 2. Repository Scope

## 2.1 扫描根范围

目标扫描根目录：

```text
E:/nas/JR/SynologyDrive/KG_康邻/研发/平台/KG_20260727
```

扫描对象不仅包括未来被 Git 跟踪的文件，也包括应被排除的本地产物。只有先识别全部内容，才能证明 `.gitignore` 与 Allowlist 没有遗漏。

## 2.2 分区范围

| 范围 | 必须检查的内容 | 主要风险 |
|---|---|---|
| `backend/` | 应用、测试、Alembic 历史、Seed、脚本、配置读取逻辑、依赖声明 | 数据库凭据、JWT Secret、测试账号、真实数据、Dump |
| `frontend/` | 源代码、静态资源、测试、构建配置、依赖及锁文件 | API Token、环境变量、用户截图、构建产物 |
| `docs/` | P1/P2 文档、Runbook、UAT Evidence、Handoff、附件 | 账号密码、内部 URL、PII/PHI、截图与报告数据 |
| `scripts/` | Python、PowerShell、Shell、Seed、部署与治理脚本 | 硬编码凭据、危险数据库目标、导出文件路径 |
| `configs/` | 应用、Alembic、Vite、pytest、CI、Deploy 和环境模板 | Secret、真实连接串、环境混用 |

`configs/` 是逻辑分类，不要求当前必须存在同名目录。它覆盖根目录及各子目录中的 `pyproject.toml`、`alembic.ini`、package 配置、Vite 配置、测试配置、环境模板以及未来 CI/Deploy 配置。

## 2.3 P2 文档边界

当前 P2 权威文档位于 `KG_28/docs/P2`。若后续按批准方案迁入 `KG_20260727/docs/P2`，迁入候选必须先进入本 Gate 的扫描范围；未经扫描和 Allowlist 审批，不得随 Commit 0 或 Commit 1 一并纳入。

## 2.4 明确不纳入业务开发

本次范围盘点不得借机创建或修改：

- Decision-003 Family Authorization 对象；
- Decision-008 Health Fact Correction 对象；
- Decision-009 Service Relationship 对象；
- P2 Schema、Migration、ORM、Repository、Service 或 API。

---

# 3. Secret Detection Strategy

## 3.1 检测类别

必须检查以下 Secret 或疑似 Secret：

- `password`、`passwd`、`pwd` 及其变体；
- access token、refresh token、personal access token、Bearer token；
- API key、client secret、webhook secret；
- JWT 签名密钥、Session Secret、加密密钥；
- 数据库 URL、用户名、密码及带认证信息的连接串；
- SSH private key、PEM private key、PFX/P12、Service Account；
- 证书文件及其是否与私钥、内部域名或真实环境绑定；
- `.env`、`.env.*`、环境导出、Shell History、日志和调试输出；
- 文档、测试、Seed、截图、注释及示例中的真实账号或凭据。

## 3.2 扫描方法

未来获准执行时采用分层检查：

1. **文件名检查**：识别 `.env`、key、certificate、credential、secret、backup 等高风险名称；
2. **规则检查**：使用供应商 Token 特征、私钥头、认证 URL 和常见赋值模式；
3. **高熵检查**：识别不符合已知前缀但疑似随机密钥的长字符串；
4. **归档检查**：在隔离环境中检查允许展开的压缩包内容；
5. **人工复核**：审阅配置、Seed、测试账号、Runbook、截图和扫描误报；
6. **候选 Tree 复扫**：Allowlist 形成后再次扫描，确认实际 Commit 候选无遗漏。

扫描证据不得记录 Secret 明文，只记录 Finding ID、文件位置、类别、严重性、不可逆指纹和处置状态。

## 3.3 `.env` 策略

- `.env` 与真实环境变体禁止进入 Git；
- 经审查的 `.env.example` 可以进入 Allowlist，但只能包含无效占位值；
- `.gitignore` 已覆盖 `.env` 不代表可以跳过盘点；
- 环境文件曾通过聊天、共享盘或旧仓库传播时，相关 Credential 必须轮换；
- Development、UAT、Staging、Production 使用独立 Secret，不得复用。

## 3.4 处置规则

| Finding | 处置 |
|---|---|
| 有效或疑似有效凭据 | 立即阻塞；先撤销/轮换，再清理候选文件 |
| 已失效凭据 | 清除或替换，并提供失效证明 |
| 合成测试密码 | 人工确认无法用于任何环境，并明确标注测试用途 |
| 公开证书 | 核验不含私钥、真实身份风险及非必要内部信息 |
| 误报 | 由安全负责人记录理由并批准关闭 |

任何曾在对话中公开的账户密码都应视为已暴露，禁止继续用于 GitHub、Remote 或 CI。

---

# 4. PII / PHI Detection Strategy

## 4.1 检测目标

确认 Git 候选中不存在未经授权的真实身份数据、客户资料或健康信息，避免永久 Git 历史成为个人数据和健康数据的非受控存储。

## 4.2 PII 范围

- 姓名、手机号、证件号、地址、生日、性别；
- 银行卡、支付标识、订单联系人；
- User、Member、员工、Therapist 与客户之间的可识别关联；
- 登录账号、认证材料、设备标识和可定位个人的日志；
- 机构客户名单、服务记录和联系信息。

## 4.3 PHI 范围

- Health Profile、Health Fact、Health Indicator；
- 检测、Assessment、Report、Plan 与健康风险；
- 病史、用药、诊断、症状、生活方式及测量值；
- Member 与健康记录、机构、门店或专业人员的关联；
- 健康授权、服务记录和 AI 健康输出。

## 4.4 检测介质

- 源代码常量、Fixture、Seed 和测试快照；
- Markdown、Word、PDF、Excel、CSV、JSON；
- 图片、UAT 截图、录屏和附件；
- 日志、错误栈、查询结果和数据库导出；
- 文件名、目录名及压缩包内文件。

文本规则必须结合人工语义复核；图片和 PDF 需要 OCR 或人工检查。仅替换姓名但保留真实健康数据不构成充分脱敏。

## 4.5 数据准入规则

| 数据类型 | Git准入 |
|---|---|
| 明确的合成测试数据 | 经人工复核后可进入相应 Commit Allowlist |
| 已充分匿名化且确有工程必要的数据 | 合规与数据 Owner 双重审批 |
| 真实客户身份信息 | 禁止 |
| 真实 Health Fact、检测或报告 | 禁止 |
| 未脱敏截图、日志或导出 | 禁止 |
| 无法确认来源的数据 | 禁止，直到完成来源确认 |

---

# 5. Large File Detection

## 5.1 检测类别

- AI/ML 模型、权重、Embedding 和训练数据；
- 图片、设计稿、截图、音频和视频；
- 数据库文件、数据目录和查询导出；
- ZIP、7Z、RAR、TAR、GZ 等压缩包；
- 日志、Coverage、构建产物和二进制 Artifact；
- PDF、Office 文档及其他不易进行差异审查的文件。

## 5.2 项目阈值

| 单文件大小 | Gate处理 |
|---|---|
| `< 5 MiB` | 仍需按内容与类型检查 |
| `5–20 MiB` | Warning；必须说明用途、Owner 和保留理由 |
| `> 20 MiB` | 默认阻塞；需技术与安全双重例外审批 |
| 数据库、压缩包、模型或 PHI 文件 | 不受大小影响，进入专项阻塞检查 |

托管平台允许的最大文件大小不是项目放行标准。Git LFS 不得用于绕过 Secret、PII/PHI 或数据库文件禁令；只有经批准的非敏感二进制资产才可另行评估。

## 5.3 默认排除

`.venv/`、`node_modules/`、`dist/`、Coverage、缓存、日志及临时 Artifact 必须保持在 Git 之外，并通过候选 Tree 清单验证忽略规则生效。

---

# 6. Database Artifact Detection

## 6.1 检测目标

候选范围必须检查：

- SQL dump、全量 Schema/Data 导出；
- `.dump`、`.backup`、`.bak`；
- SQLite、DB 文件及本地应用数据库；
- PostgreSQL 数据目录、WAL、base、global、Volume；
- TimescaleDB Chunk、数据目录、备份和快照；
- CSV、JSON、Excel 查询导出及数据库日志；
- 文件扩展名被修改但内容具备数据库或归档特征的文件。

## 6.2 Migration 与数据库 Artifact 区分

- 已有 Alembic Migration 历史是 P1 可追溯资产，应保留并审查；
- 包含环境业务数据、批量 INSERT/COPY 数据、完整数据库对象快照的文件属于 Dump；
- 合成 Seed 只有在确认无真实 PII/PHI、无 Secret 且具备测试必要性后才能纳入；
- UAT、Development、Integration、Staging 和 Production 数据库文件都不得进入 Git。

## 6.3 放行条件

Initial Commit 候选 Tree 中数据库 Dump、运行数据库、PostgreSQL/TimescaleDB 数据目录和真实查询导出必须为零。

---

# 7. Synology ReparsePoint Validation

## 7.1 风险背景

Repository Root 位于 SynologyDrive 同步目录。ReparsePoint 或按需下载文件可能未完全物化；同步过程还可能在扫描与 Commit 之间改变内容。若不验证，扫描对象、Git 读取对象和远端 Clone 结果可能不一致。

## 7.2 必须验证

1. **文件物化**：所有 Allowlist 候选均已下载到本地，可完整读取，不是占位对象；
2. **Git 读取**：Git 看到的是稳定文件内容，而非不可移植链接或同步占位符；
3. **Hash 一致性**：扫描前 Manifest Hash、扫描后 Hash 与未来 Stage 前 Hash 一致；
4. **同步稳定性**：扫描窗口内不存在并发编辑、部分同步或冲突副本；
5. **跨环境一致性**：中文路径、Unicode、大小写、长路径和换行符在 Windows 与未来 Linux CI 中可复现；
6. **干净 Clone 验证**：未来 Commit 后在独立目录 Clone，并与批准 Manifest 对比。

## 7.3 推荐执行边界

未来扫描应针对完全物化的隔离只读快照执行，而不是直接信任活动同步目录。扫描前建立短时写入冻结窗口，扫描后再次计算 Hash；任何变化都会使扫描结论失效。

Synology 版本历史不能替代 Git Commit、Artifact 或安全审计。

---

# 8. Initial Commit Allowlist

## 8.1 Commit 0 — Repository Policy Baseline

Commit 0 只允许包含建立仓库治理边界所需的最小文件：

- 根 `README.md`；
- 根 `.gitignore`；
- 经批准的仓库范围、分支、贡献和安全规则；
- 经批准迁入仓库的 Git 初始化、安全 Gate 与追踪说明；
- 不包含业务代码变更、P2 实现、数据库文件或运行配置。

Commit 0 的目的只是固定 Repository Policy，不表示 Phase 1 开发已经开放。

## 8.2 Commit 1 — P1 Code Baseline

Commit 1 允许在完成全量扫描和人工复核后纳入：

- Backend 应用源代码、测试、现有 Alembic 历史和必要脚本；
- Backend 依赖声明和不含 Secret 的配置模板；
- Frontend 源代码、必要配置、依赖声明和锁文件；
- 根 `scripts/` 中经审查的治理/验证脚本；
- 经批准的 P1 文档、UAT Runbook 与脱敏 Evidence；
- 经批准迁入的 P2 架构文档；
- P1 当前 Revision、测试基线和已知风险说明。

Commit 1 必须证明 P1 历史语义未被修改，且不混入任何 P2 Phase 1 代码实现。

## 8.3 禁止进入 Git

- `.env`、真实配置、密码、Token、API Key、私钥和敏感证书；
- `.venv/`、`node_modules/`、`dist/`、缓存、Coverage、日志；
- SQL Dump、Backup、SQLite、PostgreSQL/TimescaleDB 数据目录；
- 真实 PII/PHI、客户资料、健康记录和未脱敏 Evidence；
- 未批准图片、视频、压缩包、模型和大文件；
- IDE/OS 私有文件、Synology 冲突副本和同步临时文件；
- NAS 上与 `KG_20260727` Monorepo 无关的产品或历史资料；
- Decision-003、Decision-008、Decision-009 的任何正式实现；
- 未在批准 Manifest 中出现的其他文件。

## 8.4 Stage 规则

- 必须根据 Allowlist 显式选择文件；
- 禁止使用未经清单审核的全目录 Stage；
- `.gitignore` 不是 Allowlist；
- Stage 后必须重新生成候选 Tree 清单并复扫；
- Commit 0 与 Commit 1 必须分开，不能压缩为一个无法审查的 Initial Commit。

---

# 9. Scan Evidence Requirements

## 9.1 必须输出的报告

后续正式扫描至少形成：

1. Repository Inventory Report；
2. Secret Scan Report；
3. PII/PHI Review Report；
4. Large File Report；
5. Database Artifact Report；
6. Synology Materialization and Hash Report；
7. Initial Commit Allowlist Manifest；
8. Finding Disposition and Approval Record。

## 9.2 Hash要求

- 记录扫描范围的文件级 Hash 和整体 Manifest Hash；
- 记录扫描工具、版本、规则版本与配置 Hash；
- Allowlist、候选 Tree 和最终 Commit 必须能映射到同一批准快照；
- Hash 不一致时不得复用先前报告；
- Evidence 不得包含 Secret 明文或真实 PHI。

## 9.3 时间与责任人

每份报告必须包含：

- 扫描开始、结束时间及时区；
- 执行人、技术 Reviewer、安全 Reviewer；
- PII/PHI 的合规/健康数据 Reviewer；
- Repository Owner 与 P1 维护负责人；
- Finding Owner、处置期限与关闭审批；
- 对应 Task、后续 Commit SHA、CI Run 或 Artifact 的预留关联字段。

## 9.4 Evidence访问

详细 Finding 只向最小必要角色开放。报告使用文件定位、风险类别和不可逆指纹，不复制凭据或健康数据。Evidence 自身也必须进入扫描与保留策略。

---

# 10. Security Approval Gate

## 10.1 Gate Checklist

- [ ] Repository Scope 和 Root 获得技术负责人批准
- [ ] 所有候选文件已完整物化
- [ ] 扫描前后 Manifest Hash 一致
- [ ] 根 `.gitignore` 已通过人工复核
- [ ] Commit 0 Allowlist 已批准
- [ ] Commit 1 Allowlist 已批准
- [ ] Secret 扫描无未处置 Critical/High Finding
- [ ] 已暴露或疑似暴露的 Credential 已轮换
- [ ] `.env`、私钥和真实连接串均未进入候选 Tree
- [ ] PII/PHI 检查无未经授权的真实数据
- [ ] 大文件、模型、媒体和压缩包均已审查
- [ ] 数据库 Artifact 检查结果为零
- [ ] Synology ReparsePoint 与干净读取验证通过
- [ ] 扫描报告、Hash、时间和责任人记录完整
- [ ] 安全、合规、技术、Repository Owner 和 P1 维护负责人完成审批
- [ ] Decision-003、Decision-008、Decision-009 继续保持 Blocked

## 10.2 Hard Block

出现以下任一情况，首次 Commit 必须保持 `NOT OPEN`：

- 存在未轮换密码、Token、API Key 或私钥；
- 发现真实 PII/PHI、客户资料或健康数据；
- 存在 SQL Dump、Backup、SQLite 或 PostgreSQL/TimescaleDB 数据目录；
- 候选大文件没有必要性和审批；
- 文件未物化或 Hash 不稳定；
- 扫描范围、规则或结果不可复现；
- Commit 候选不符合 Allowlist；
- Evidence 缺少责任人或审批；
- 任何 Reviewer 要求暂停。

## 10.3 Rollback原则

Gate 失败时停止 `git add`、`git commit` 和 Remote 配置。对有效凭据先撤销/轮换，再从隔离候选快照中清理；不得覆盖或删除 P1 源文件。修正后重新生成 Manifest 并执行完整扫描，不沿用失效审批。

## 10.4 Final Decision

当前只完成本地 Git 空仓库、`main` 分支和根 `.gitignore`。尚未执行本计划定义的任何扫描，也没有形成 Commit 0/Commit 1 的批准 Evidence，因此 Initial Commit 和 P2 Phase 1 Implementation 继续关闭。

```text
P2 Pre Initialization Security Scan Plan:
REVIEW READY

P2 Git Initial Commit:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

## Final Compliance Check

- [x] 仅生成安全扫描 Gate 方案
- [x] 未执行 Secret 扫描
- [x] 未执行 PII/PHI 扫描
- [x] 未执行大文件或数据库 Artifact 扫描
- [x] 未执行 `git add`
- [x] 未执行 `git commit`
- [x] 未创建或配置 Remote
- [x] 未修改应用代码或数据库
- [x] 未创建 Decision-003、Decision-008、Decision-009 对象
- [x] P2 Git Initial Commit 保持 `NOT OPEN`
- [x] P2 Phase 1 Implementation 保持 `NOT OPEN`

**下一步仅允许人工评审本计划。安全扫描执行必须获得单独授权。**
