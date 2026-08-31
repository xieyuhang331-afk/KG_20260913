# 一期切片5 Web前端检查点A本地验收记录

## 1. 基线与授权边界

- 分支：`feature/phase1-slice5-web-frontend-closeout`
- 开始基线：`d7689bf58949a070ae39c6c44e0df17841f564a5`
- 本检查点仅覆盖健康评估摘要、机构高风险任务和平台高风险任务只读监督。
- 医学规则治理、规则草稿、医学审核和原因码治理页面继续冻结，等待后端兼容Hotfix。
- Backend、Migration、SQL、CI Workflow、`package-lock.json`和依赖版本均未修改。

## 2. 已完成范围

### 机构端

- 在客户健康档案中接入健康评估摘要，只展示服务端评估状态、风险分级、规则版本和时间。
- 新增高风险任务列表、详情及正式结构化Action。
- `org_admin`与`org_operator`沿用现有机构端顶层权限，可按服务端状态机执行获批Action。
- 409只刷新权威详情，不自动重放。
- `COMMIT_OUTCOME_UNKNOWN`先重新查询；再次确认时复用完全相同的请求体和Idempotency-Key。
- 转介和解除必须由操作人选择联系结果及结构化建议，不由前端猜测事实。
- `assignee`只显示“已分配/待分配”，不展示内部整数ID。

### 平台端

- 新增高风险任务监督列表与详情。
- 平台页面仅提供只读监督，不暴露机构Action。
- 不展示原始健康数据、内部人员ID或内部异常。

### 合同与安全

- 机构路径固定使用单数`/api/v1/institution`。
- cursor仅作为opaque string透传，不解析、不生成、不推导总页数。
- 401/403/404/409/422/503统一映射为安全中文状态。
- 页面未知状态fail-closed。

## 3. Test First证据

- Expected RED：新增客户端合同测试首先因`./slice5`不存在而失败，证明测试确实覆盖新合同边界。
- Slice 5新增定向测试：20项全部通过。
- 原健康档案定向回归：11项全部通过。
- 完整前端回归：19个测试文件、213项测试全部通过，failure/error/skipped为0/0/0。

## 4. 静态质量门禁

- TypeScript：PASS。
- Production Build：PASS。
- 基线Lint：PASS。
- Slice 5白名单Lint：PASS。
- Slice 5变更文件CRLF-safe Format Check：PASS。
- PII/PHI/Credential扫描：PASS，命中0。
- `git diff --check`：PASS。

说明：仓库既有全局`format:check`仍会对37个未改历史CRLF文件报告换行格式差异；本检查点没有批量格式化或夹带整改这些历史文件，使用精确变更文件的CRLF-safe门禁验收。

## 5. 当前状态

```text
SLICE 5 WEB FRONTEND CHECKPOINT A:
COMPLETED LOCALLY

MEDICAL RULE GOVERNANCE:
BLOCKED — WAITING FOR BACKEND HOTFIX

PUSH / PR / CI:
NOT OPEN
```
