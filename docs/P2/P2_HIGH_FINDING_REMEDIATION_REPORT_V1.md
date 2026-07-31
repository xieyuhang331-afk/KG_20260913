# P2 High Finding Remediation Report V1

> Findings：FIND-SEC-001、FIND-SEC-002  
> 日期：2026-07-31（Asia/Shanghai）  
> 范围：应用Secret配置、P1 Seed Credential及直接相关测试  
> 状态：REVIEW READY

# 1. 修改文件

## 应用与Seed

- `backend/app/core/config.py`
- `backend/scripts/seed_p1_test_data.py`

## 相关测试

- `backend/tests/test_config_engineering.py`
- `backend/tests/test_database_alembic.py`
- `backend/tests/test_seed_p1_test_data_script.py`
- `backend/tests/integration/test_seed_p1_reset_real_db.py`

# 2. 修改原因

## FIND-SEC-001

数据库密码和JWT Secret原先具有具体默认值。环境变量缺失时应用会继续使用可预测Credential，违反Fail Closed原则，并会把Secret固化到Git历史。

## FIND-SEC-002

P1 Seed脚本原先包含固定登录密码。该值可用于Seed/UAT账号，不应作为可复用Credential进入代码版本历史。

# 3. 修改内容

## 应用配置

- `Settings.database_password`和`Settings.jwt_secret_key`改为必填字段；
- 新增统一的必需Secret读取函数；
- `KG_DATABASE_PASSWORD`缺失或空白时立即失败；
- `KG_JWT_SECRET_KEY`缺失或空白时立即失败；
- 保留数据库Host、Port、Name、User及JWT算法等非Secret默认值；
- 未改变数据库URL结构、JWT算法或Token生命周期。

## Seed Credential

- 删除固定Seed密码常量；
- Seed密码改由`KG_P1_SEED_PASSWORD`运行时提供；
- 环境变量缺失或空白时立即失败；
- 密码只用于生成Hash，不在计划输出中打印；
- 未改变P1 Seed用户、机构、门店、健康数据、Reset顺序或Upsert行为。

## 测试

- 新增数据库密码缺失Fail Closed测试；
- 新增JWT Secret空白Fail Closed测试；
- 新增Seed密码缺失Fail Closed测试；
- 测试Credential改为运行时随机生成；
- 直接构造`Settings`的测试显式提供随机Secret；
- 移除测试中与旧默认值相同的固定数据库URL密码。

# 4. 安全影响

## 改善

- Secret不再通过代码默认值进入Git候选树；
- 配置遗漏会在应用读取设置时立即暴露，不会静默使用弱默认值；
- Seed运行者必须显式提供Credential，可按环境和执行批次轮换；
- 单元测试不依赖固定可复用密码；
- FIND-SEC-001、FIND-SEC-002复扫状态均为Resolved。

## 运维影响

启动Backend前必须提供：

- `KG_DATABASE_PASSWORD`；
- `KG_JWT_SECRET_KEY`。

执行P1 Seed前还必须提供：

- `KG_P1_SEED_PASSWORD`。

本次未创建`.env`、未修改部署配置，也未写入任何真实Secret。运行环境配置由后续受控运维流程负责。

# 5. 测试结果

## 指定测试

```text
26 tests passed
```

覆盖：配置读取、Fail Closed、数据库URL、Alembic对象、Seed Credential、Seed计划、SQL顺序、幂等与Rollback。

## Backend回归

```text
404 tests executed
403 passed
1 failed
```

唯一失败由缺失`docs/governance/ddl-module-map.yaml`导致，与本次修改无关。未运行真实数据库Integration测试，未修改数据库。

# 6. Rollback方式

如需回滚，仅回退本报告列出的6个文件中与Secret来源相关的改动，并重新运行26个指定测试。

安全限制：

- 不得恢复原数据库密码默认值；
- 不得恢复原JWT Secret默认值；
- 不得恢复固定Seed/UAT密码；
- 若旧运行方式必须兼容，应通过受控环境变量注入，而不是代码回退值；
- 回滚后必须重新执行Secret Scan，任何固定Credential都会重新阻塞Initial Commit。

# 7. 最终状态

```text
FIND-SEC-001:
RESOLVED — WAITING FOR HUMAN REVIEW

FIND-SEC-002:
RESOLVED — WAITING FOR HUMAN REVIEW

P2 Pre Initialization Security Scan Execution:
REVIEW READY

P2 Git Initial Commit:
NOT OPEN

P2 Phase 1 Implementation:
NOT OPEN
```

未执行`git add`、`git commit`、Remote配置或数据库修改。
