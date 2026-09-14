# 一期发布证据 Manifest 模板 V1

## 模板声明

- 本文件只是模板，不是实际 Release Manifest，不代表发布候选、可部署、已部署或 APP 可交接。
- 实例化时只能填写脱敏计数、状态、版本和 Hash；禁止填写 Secret、Token、Credential、带凭据 URL、PII 或 PHI。

## 状态

- 当前状态：`CANDIDATE | CI_VERIFIED | DEPLOYABLE | DEPLOYED | APP_HANDOFF_READY`
- 状态裁决人：`<role>`
- 裁决时间：`<UTC timestamp>`
- 上一状态证据 SHA-256：`<hash or NOT APPLICABLE>`

## Source

- Repository：`<owner/repository>`
- Commit SHA：`<sha>`
- Tree SHA：`<sha>`
- Branch / Tag：`<name>`
- PR：`<number>`
- Merge Commit / Parents：`<sha list or NOT MERGED>`
- Ancestry：`PASS | NOT RUN`
- tracked/staged/untracked：`<counts>`

## Database

- Migration single Head：`<revision>`
- Revision graph SHA-256：`<hash>`
- upgrade/downgrade/re-upgrade：`PASS | NOT RUN | NOT APPLICABLE`
- Role/ACL matrix：`<evidence hash>`
- 真实数据处理：`NOT AUTHORIZED | NOT EXECUTED | <separate evidence>`

## API

- OpenAPI SHA-256：`<hash>`
- Breaking diff：`0 | <versioned explanation>`
- API version：`<version>`
- APP compatibility：`UNVERIFIED | ACCEPTED DEFERRED RISK | VERIFIED`

## Python Toolchain

- Python / uv / Ruff：`<exact versions>`
- `pyproject.toml` SHA-256：`<hash>`
- `uv.lock SHA-256`：`<hash>`
- `uv lock --check` / frozen sync：`PASS | NOT RUN`

## Frontend Toolchain

- Node / npm：`<exact versions>`
- `package.json` SHA-256：`<hash>`
- `package-lock.json SHA-256`：`<hash>`
- `npm ci`：`PASS | NOT RUN`
- Build / Vitest / lint / source PII / delivery scan：`<statuses>`
- Format：`PASS | DEBT ACCEPTED | RELEASE BLOCKED | NOT RUN`

## CI and JUnit

- Workflow SHA-256：`<hash>`
- CI Run / Attempt / Head：`<ids>`
- Four jobs：`<status matrix>`
- Backend JUnit：`<artifact hashes and recursive counters>`
- Frontend JUnit：`<artifact hash and recursive counters>`
- failures/errors/skipped：`<counters>`
- Required Checks：`VERIFIED | NOT VERIFIED`

## Artifacts

- Frontend dist SHA-256 manifest：`<hash>`
- Frontend safe diagnostic summary SHA-256：`<hash>`
- Backend candidate artifact：`<hash or NOT GENERATED>`
- Workflow summary：`<hash or NOT GENERATED>`
- 实际 Release Manifest：`<hash or THIS DOCUMENT>`

## Security

- Source PII scan：`PASS | NOT RUN`
- JUnit/dist/summary/Manifest delivery scan：`PASS | NOT RUN | NOT GENERATED`
- Secret/Token/PII/PHI findings：`0 | <approved redacted exception count>`
- 扫描规则版本/Hash：`<identifier>`

## 资源清理

- Database / Role：`0 residual | NOT USED`
- RabbitMQ / Worker：`0 residual | NOT USED`
- Container / Network / Volume：`0 residual | NOT USED`
- Process / Temporary artifact：`0 residual`
- Cleanup Evidence SHA-256：`<hash>`

## Environment

- Profile：`localhost_ephemeral | ci_ephemeral | production`
- 环境变量：`<names and formats only>`
- 外部依赖：`<version/status without endpoint or credential>`
- Backup / rollback：`<approved evidence or NOT AUTHORIZED>`

## 延期风险与限制

- 延期风险：`<redacted list>`
- 未执行项：`<list>`
- 不可从当前状态推导的下一状态：`<list>`
- APP 正式交接：`HOLD | AUTHORIZED` 

## 签署

- Evidence Owner：`<role/signature hash>`
- Technical Reviewer：`<role/signature hash>`
- Merge Owner：`<role/signature hash or NOT MERGED>`
- Deployment Owner：`<role/signature hash or NOT DEPLOYED>`
