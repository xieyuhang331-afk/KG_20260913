# 一期切片5 Web 前端截图索引

以下截图均由真实 FastAPI 与 Fresh Disposable PostgreSQL 返回的合成数据生成，不含真实 PII、PHI、Token、Credential、Database URL 或内部异常。

| 页面 | 视口 | 文件 | SHA-256 | 验收结果 |
|---|---:|---|---|---|
| 医学规则治理 | 1440×900 | `截图/医学规则治理-1440x900.png` | `aac5930136d21a2b5c46f83ea953047a6cacf26f9b9f733cbfea8d9ae6f88907` | 无横向溢出，Console Error 0 |
| 医学规则治理 | 1280×800 | `截图/医学规则治理-1280x800.png` | `dea92483f304227b010b0c1e752537621b56d31d3611f9b1cdba1d526b6d3869` | 无横向溢出，Console Error 0 |
| 医学规则治理 | 1024×768 | `截图/医学规则治理-1024x768.png` | `1384612ce047695aeb291cbbac02a81ed7b092bdd3f2b82af173e4991f7e66a9` | 无横向溢出，Console Error 0 |
| 医学规则版本详情 | 1440×900 | `截图/医学规则版本详情-1440x900.png` | `b5e8b56c03fb9ab90b64cc5e4c14139d886ddfce41534973f98a8181002c09f8` | 双栏清晰，Console Error 0 |
| 医学规则版本详情 | 1280×800 | `截图/医学规则版本详情-1280x800.png` | `1f36922626fda5eb5244c3b3d0b6273f6de4864e555318ca364df9647bb342c2` | 无控件截断，Console Error 0 |
| 医学规则版本详情 | 1024×768 | `截图/医学规则版本详情-1024x768.png` | `c58441093c590c5a20e9a2160048748fdc35e0c49bcd729cc15aaa56ef5cb568` | 安全收窄，无横向溢出 |

## 页面—接口映射

| 页面 | 真实接口 | 角色 | 写操作边界 |
|---|---|---|---|
| 医学规则治理列表 | `GET /api/v1/platform/assessment-rule-sets` | expert / sys_admin / super_admin | cursor 不透明透传 |
| 医学规则版本详情 | `GET /api/v1/platform/assessment-rule-sets/{version_id}` | expert / sys_admin / super_admin | 详情使用闭合 DTO |
| 草稿与医学审核 | create / update-draft / submit / review | expert | 只使用正式动作及原因码 |
| 生命周期治理 | publish / suspend / resume / retire | sys_admin / super_admin | 二次确认、expected_version、幂等键 |

## 未展示或明确禁止

- 自由 JSON、医学阈值编辑、23 项延期规则启用。
- 内部人员整数 ID、摘要密钥、原始审计 Payload。
- 后端未定义的拒绝动作或原因码。
