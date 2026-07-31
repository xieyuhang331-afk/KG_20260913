# KG_20260727

`KG_20260727` 是康邻健康管理平台从 2026-07-27 起重新建立的项目文档主线。

本项目位于 `KG_康邻/研发/平台/KG_20260727`，不是 `KG_project` 的子目录。`KG_project` 作为历史项目和迁移来源保留；后续新主线必须严格遵守 726 交付包的三类来源：

1. `../../../../Tech/KG_project/康邻健康管理平台726/PRD文档`
2. `../../../../Tech/KG_project/康邻健康管理平台726/架构方案`
3. `../../../../Tech/KG_project/康邻健康管理平台726/产品原型/系统原型`

## 三源最高依据

### PRD 文档

`PRD文档` 共 39 份，是需求、旅程、领域、权限、业务对象、界面、字段和验收的产品依据。

### 架构方案

`架构方案` 共 9 个核心文件，是技术架构、API、DDL、AI、上传、种子数据、通知和 Prompt 工程的实现依据。

### 产品原型

`产品原型/系统原型` 是页面结构、交互、视觉密度、端模块、演示截图和验收体验的依据。

## 文档入口

- `docs/PROJECT_CHARTER.md`
- `docs/BASELINE.md`
- `docs/MIGRATION_STRATEGY.md`
- `docs/GOVERNANCE.md`
- `docs/ROADMAP.md`
- `docs/BACKEND_MODULE_BASELINE.md`
- `docs/DDL_MIGRATION_BASELINE.md`
- `docs/API_CONTRACT_BASELINE.md`
- `docs/PROTOTYPE_PAGE_BASELINE.md`
- `docs/ONBOARDING_REVIEW_MIGRATION_BASELINE.md`
- `docs/AI_ENGINE_BASELINE.md`
- `docs/UPLOAD_NOTIFICATION_BASELINE.md`
- `docs/BACKEND_SKELETON_BASELINE.md`
- `docs/BACKEND_CONFIG_BASELINE.md`
- `docs/BACKEND_DATABASE_ALEMBIC_BASELINE.md`
- `docs/governance/source-baseline.yaml`
- `docs/governance/source-index.yaml`
- `docs/governance/prd-index.yaml`
- `docs/governance/backend-module-map.yaml`
- `docs/governance/ddl-module-map.yaml`
- `docs/governance/api-contract-map.yaml`
- `docs/governance/prototype-page-map.yaml`
- `docs/governance/onboarding-review-map.yaml`
- `docs/governance/ai-engine-map.yaml`
- `docs/governance/upload-notification-map.yaml`
- `docs/governance/kg-project-migration-matrix.yaml`
- `docs/governance/legacy-file-audit.yaml`
- `docs/handoffs/CURRENT.md`

## 当前状态

- D37: 项目文档主线重建，`DONE`
- D38: 三源治理门禁和旧文件迁移审计，`DONE`
- D39: PRD 需求与验收机器索引，`DONE`
- D40: 后端模块结构设计基线，`DONE`
- D41: DDL 与迁移基线，`DONE`
- D42: API 契约基线，`DONE`
- D43: 产品原型页面基线，`DONE`
- D44: 入驻与审核闭环迁移基线，`DONE`
- D45: AI 引擎基础基线，`DONE`
- D46: 文件上传与通知基础基线，`DONE`
- D47: 后端工程骨架，`DONE`
- D48: 后端依赖与配置工程化，`DONE`
- D49: 数据库连接与 Alembic 初始化，`DONE`

## 核心原则

1. PRD、架构方案、产品原型共同构成最高约束。
2. 三源冲突时，不得自行取舍，必须记录冲突并等待 Owner Decision。
3. `KG_project` 的历史开发能迁移就迁移，不能迁移就归档。
4. 后续任务必须先经过治理文档、来源映射和迁移矩阵。

## 验证

```powershell
python scripts\build-prd-index.py
python scripts\build-backend-module-map.py
python scripts\build-ddl-module-map.py
python scripts\build-api-contract-map.py
python scripts\build-prototype-page-map.py
python scripts\build-onboarding-review-map.py
python scripts\build-ai-engine-map.py
python scripts\build-upload-notification-map.py
python scripts\validate-governance.py
$env:PYTHONPATH='backend'
python -m unittest backend.tests.test_app_contract -v
```
