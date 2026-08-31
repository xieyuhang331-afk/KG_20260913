# 一期切片5 Web 前端验收摘要

## 范围与基线

- 分支：`feature/phase1-slice5-web-frontend-closeout`
- 检查点 A：`bd4a738cda66e2c648eb60a96295f7a7ea2a44a8`
- 后端兼容合同合并基线：`38d7237edc80398a953a25fbfd0266d102487358`
- Migration Head：`20260830_0033`
- 前端范围：机构端健康评估摘要与高风险任务；平台端高风险监督与医学规则治理。
- 后端、Migration、SQL、CI Workflow 与 `package-lock.json`：前端人为差异为 0。

## 正式合同结论

- 医学规则页面只使用 develop OpenAPI 中的闭合规则 DTO，不提供自由 JSON 或阈值编辑器。
- 医学审核正式动作是 `APPROVE` 与 `NEEDS_CORRECTION`；未伪造不存在的拒绝动作。
- 生命周期操作仅在服务端允许的状态和角色下展示；未知状态 fail-closed。
- cursor 作为签名不透明字符串透传，不解析、不生成、不推导总页数。
- `409` 仅刷新权威状态；未知提交使用同一请求体和同一 Idempotency-Key 查询确认。
- 页面仅展示安全公开人员引用，不展示内部人员整数 ID、摘要密钥或原始审计载荷。

## 自动化门禁

- Checkpoint A 定向测试：20/20 PASS。
- 最终完整 Vitest：223/223 PASS，20 个测试文件，failure/error/skipped = 0/0/0。
- TypeScript 与 Production Build：PASS（1953 modules transformed）。
- Slice 5 Lint：16 files PASS。
- Slice 5 changed-file Format：16 files PASS。
- PII/PHI/Credential Scan：0 命中。
- `git diff --check`：PASS；仅存在 Windows CRLF 提示，无空白错误。

## Fresh Disposable 真实 HTTP

- 使用 Fresh Disposable PostgreSQL、正式 Migration 0033、正式 Runtime 角色与真实 FastAPI。
- 创建、列表、详情、草稿修改、提交审核、医学批准、发布、暂停、恢复、退役及幂等回放：PASS。
- 状态码证据：`200 × 10`、`201 × 1`、`401 × 1`、`403 × 1`、`404 × 1`、`409 × 1`、`422 × 3`。
- 真实依赖不可用：503 PASS；前端未将 503 包装成成功或其他错误。
- Audit / Outbox / Idempotency Receipt：8 / 8 / 8，幂等回放无重复记录。
- Disposable 数据库、容器、端口、临时目录和一次性凭据残留：0。

## 浏览器验收

- 页面：医学规则治理列表、医学规则版本详情。
- 视口：1440×900、1280×800、1024×768。
- 横向溢出：0。
- Console Error：0。
- Loading 残留、控件截断、内部异常与敏感字段展示：0。
- 演示数据由真实 API 与 Fresh Disposable 数据库提供，不以 Mock 作为完成证据。

## 最终状态

```text
CHECKPOINT A:
PASS

CHECKPOINT B MEDICAL RULE GOVERNANCE:
COMPLETED LOCALLY

REAL HTTP / VISUAL ACCEPTANCE:
PASS

DISPOSABLE RESOURCE RESIDUAL:
0
```
