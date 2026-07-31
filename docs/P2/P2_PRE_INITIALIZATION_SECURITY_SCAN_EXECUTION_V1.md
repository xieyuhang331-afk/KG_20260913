# P2 Pre-Initialization Security Scan Execution V1

> 项目：KG_康邻智慧健康平台  
> Repository Root：`E:/nas/JR/SynologyDrive/KG_康邻/研发/平台/KG_20260727`  
> 执行日期：2026-07-31（Asia/Shanghai）  
> 执行模式：只读扫描；未执行 Stage、Commit、Remote 或数据库操作  
> 复扫前置：FIND-SEC-001、FIND-SEC-002 已完成代码修复

## Execution Status

```text
P2 Pre Initialization Security Scan Execution:
REVIEW READY

FIND-SEC-001:
RESOLVED — WAITING FOR HUMAN REVIEW

FIND-SEC-002:
RESOLVED — WAITING FOR HUMAN REVIEW

P2 Git Initial Commit:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

本次复扫未发现新的 Critical 或 High Finding。`REVIEW READY` 表示扫描证据已形成并可供人工审批，不代表自动授权 Initial Commit。

---

# 1. Repository Inventory Report

## 1.1 Git状态

| 项目 | 结果 |
|---|---:|
| Branch | `main` |
| Commit | 0 |
| Staged文件 | 0 |
| Remote | 0 |
| 根 `.gitignore` | 存在 |

## 1.2 文件统计

| 指标 | 数量/大小 |
|---|---:|
| 工作区文件（不含 `.git`） | 15,406 |
| 工作区大小（不含 `.git`） | 235,421,256 bytes（约 224.51 MiB） |
| Git候选文件 | 228 |
| 候选文件大小 | 980,478 bytes（约 0.94 MiB） |
| 被忽略文件 | 15,178 |
| 工作区ReparsePoint文件 | 8,173 |

## 1.3 候选文件分布

| 范围 | 文件数 | Bytes |
|---|---:|---:|
| `.gitignore` | 1 | 689 |
| `README.md` | 1 | 3,485 |
| `backend/` | 160 | 724,183 |
| `frontend/` | 57 | 144,902 |
| `scripts/` | 9 | 107,219 |
| **合计** | **228** | **980,478** |

```text
Repository Inventory:
PASS — SCOPE RECORDED
```

---

# 2. Secret Scan Report

## 2.1 扫描规则

已对全部228个候选文件检查：

- Private Key Header；
- GitHub/AWS等常见Token特征；
- JWT字符串结构；
- URL内嵌Credential；
- password、secret、token、API key等赋值；
- `.env`、PEM、KEY、P12、PFX及敏感文件名；
- 配置与Seed中的默认/固定Secret；
- 首轮Finding对应的旧常量引用。

## 2.2 FIND-SEC-001复核

| 检查项 | 结果 |
|---|---|
| 配置中的数据库密码默认值 | 已移除 |
| 配置中的JWT Secret默认值 | 已移除 |
| Secret缺失行为 | Fail Closed |
| 空白Secret行为 | Fail Closed |
| Secret来源 | `KG_DATABASE_PASSWORD`、`KG_JWT_SECRET_KEY` 环境变量 |
| 明文输出 | 无 |

```text
FIND-SEC-001:
RESOLVED — WAITING FOR HUMAN REVIEW
```

## 2.3 FIND-SEC-002复核

| 检查项 | 结果 |
|---|---|
| Seed固定密码常量 | 已移除 |
| Seed密码来源 | `KG_P1_SEED_PASSWORD` 环境变量 |
| 环境变量缺失/空白 | Fail Closed |
| Seed业务数据与写入逻辑 | 未改变 |
| 明文输出 | 无 |

```text
FIND-SEC-002:
RESOLVED — WAITING FOR HUMAN REVIEW
```

## 2.4 剩余启发式命中

复扫剩余2个 `CredentialUrl` 启发式命中：

- `backend/tests/test_config_engineering.py`：URL密码部分为运行时随机变量；
- `backend/tests/test_database_alembic.py`：SQLAlchemy URL脱敏断言使用 `***`。

人工复核结论：二者不包含固定可复用凭据，属于False Positive。

## 2.5 未发现项

- Private Key：0；
- 常见供应商Token：0；
- 固定JWT字符串：0；
- 候选`.env`文件：0；
- 候选PEM/KEY/P12/PFX文件：0；
- 新Critical/High Finding：0。

```text
Secret Scan:
PASS — HUMAN REVIEW REQUIRED
```

---

# 3. PII/PHI Review Report

## 3.1 检测结果

PII/PHI规则在Seed和测试源代码中识别到：

- 中国大陆手机号格式；
- 18位业务/身份格式值；
- Email格式；
- Health Profile、Health Indicator、病史与健康测量样例。

命中范围全部位于：

- `backend/scripts/seed_p1_test_data.py`；
- `backend/tests/`及`backend/tests/integration/`；
- Health业务代码、Schema与Alembic定义中的字段/对象名称。

## 3.2 人工复核

- Seed对象明确命名为P1测试数据；
- 用户ID、手机号、机构、门店、证照、Email和健康值采用规则化测试样例；
- URL使用`mock.kanglin.local`等测试域名；
- 候选树中无CSV、Excel、PDF、图片、视频或UAT截图；
- 未发现数据库导出、日志或独立客户资料文件；
- 未发现可证明来自真实客户或患者的记录。

## 3.3 限制

当前结论只适用于228个候选文件。未来迁入`docs/P1`、`docs/P2`、UAT Evidence、截图或附件时必须重新执行PII/PHI扫描。

```text
PII/PHI Review:
PASS FOR CURRENT CANDIDATE TREE — HUMAN COMPLIANCE REVIEW REQUIRED
```

---

# 4. Large File Detection Report

## 4.1 候选树

- 候选文件总大小：约0.93 MiB；
- 大于或等于5 MiB的候选文件：0；
- 候选模型、视频、压缩包：0；
- 最大候选文件：`frontend/package-lock.json`，74,828 bytes。

## 4.2 被忽略工作区

发现5个大于或等于5 MiB的文件，全部位于被忽略的`frontend/node_modules/`：

- esbuild可执行文件1个；
- TypeScript运行文件2个；
- lucide-react source map 2个。

所有5个文件均被Git ignore规则排除，不属于Candidate Commit。

```text
Large File Detection:
PASS
```

---

# 5. Database Artifact Report

## 5.1 检查范围

已检查候选文件名、扩展名和可读内容特征：

- SQL Dump；
- `.dump`、`.backup`、`.bak`；
- SQLite/DB文件；
- PostgreSQL `PG_VERSION`、base、global、WAL与数据目录；
- TimescaleDB数据目录、Chunk、Backup；
- PostgreSQL Dump Header与`COPY ... FROM stdin`；
- ZIP、7Z、RAR、TAR、GZ归档。

## 5.2 结果

- Database Artifact：0；
- Archive：0；
- 候选SQL文件：0；
- Alembic Migration为Python Schema演进文件，不是数据Dump。

```text
Database Artifact Detection:
PASS
```

---

# 6. Synology Materialization Validation Report

## 6.1 验证方法

对全部候选文件执行两轮：

1. 文件完整读取；
2. SHA-256计算；
3. Git `hash-object --no-filters`可读性检查；
4. 按路径、大小和SHA-256生成聚合Manifest Hash；
5. 两轮结果对比。

## 6.2 结果

| 指标 | 结果 |
|---|---:|
| 候选文件 | 228 |
| 完整可读 | 228 |
| Git可读 | 228 |
| 读取失败 | 0 |
| ReparsePoint候选 | 225 |
| 两轮变更文件 | 0 |
| Hash稳定 | 是 |

聚合Manifest SHA-256：

```text
63fa36de8c5091753cf0bc4d8cc4ba9d31cc9ef6c9fbe142d9612995ff398940
```

两轮Hash完全一致。225个候选虽具有ReparsePoint属性，但在本次窗口内均已成功读取并可由Git计算对象Hash。

## 6.3 后续限制

首次Commit前必须再次核对聚合Hash；任何文件变化都会使本次Manifest失效。Commit后仍需在独立目录执行干净Clone验证。

```text
Synology Materialization Validation:
PASS FOR CURRENT SNAPSHOT
```

---

# 7. Candidate Commit Allowlist Manifest

## 7.1 Manifest身份

```text
Candidate Files: 228
Aggregate SHA-256: 63fa36de8c5091753cf0bc4d8cc4ba9d31cc9ef6c9fbe142d9612995ff398940
Manifest Status: REVIEW READY — NOT APPROVED
```

## 7.2 Commit 0候选

| 文件 | 用途 |
|---|---|
| `.gitignore` | Repository排除与安全基线 |
| `README.md` | 当前项目入口与范围说明 |

Commit 0仍须人工复核README内容及根`.gitignore`完整性。

## 7.3 Commit 1候选

| 范围 | 文件数 | 说明 |
|---|---:|---|
| `backend/` | 160 | P1应用、测试、Alembic、Seed、依赖与配置代码 |
| `frontend/` | 57 | P1 Platform/Institution Web源代码与依赖声明 |
| `scripts/` | 9 | 治理与验证脚本 |
| **合计** | **226** | 不含Commit 0的2个文件 |

## 7.4 当前不在Manifest的范围

- `.venv/`、`node_modules/`、`dist/`与缓存；
- `.env`和真实环境配置；
- 数据库、日志、Dump、Backup；
- `KG_28/docs/P2`及其他Repository Root外部资料；
- 未来迁入的P1/P2文档与Evidence；
- Decision-003、Decision-008、Decision-009实现。

```text
Candidate Commit Allowlist Manifest:
REVIEW READY — NOT APPROVED
```

---

# Test Evidence

## 定向测试

```text
Command Scope:
test_config_engineering
test_database_alembic
test_seed_p1_test_data_script

Result:
26 tests passed
```

验证内容包括：

- 缺少数据库密码时Fail Closed；
- JWT Secret为空时Fail Closed；
- Seed密码缺失时Fail Closed；
- 运行时Secret读取；
- Seed Hash生成；
- P1 Seed SQL计划、重置顺序与幂等行为保持。

## Backend单元回归

```text
404 tests executed
403 passed
1 failed
```

唯一失败：`test_core_orm_models.CoreOrmModelTests.test_d50_core_specs_cover_first_six_tables`，原因是Repository Root当前缺少`docs/governance/ddl-module-map.yaml`。该失败与本次Secret修复无关，未在本任务扩大范围修复。

未运行真实数据库Integration测试，未修改数据库。

---

# Final Decision

FIND-SEC-001与FIND-SEC-002的代码风险已解除，安全复扫没有发现新的Critical/High Finding。由于Allowlist尚未获得人工批准、凭据轮换证据尚需责任人确认，Initial Commit继续保持关闭。

```text
P2 Pre Initialization Security Scan Execution:
REVIEW READY

P2 Git Initial Commit:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

## Compliance Check

- [x] 未执行`git add`
- [x] 未执行`git commit`
- [x] 未创建Remote
- [x] 未修改数据库
- [x] 未修改P1业务流程
- [x] 未修改Health数据模型
- [x] 未创建Decision-003/008/009对象
- [x] 未输出Secret明文
- [x] Initial Commit继续保持`NOT OPEN`

**下一步：由技术、安全、合规和P1维护负责人复核Finding处置、凭据轮换证据与Manifest Hash；批准前不得Stage或Commit。**
