# Spec 003：精确查询与证据回查

日期：2026-10-11。范围仅为 [roadmap S3.1](../../docs/mvp-roadmap.md#s3完成检索证据回查和问答)。文档是实施契约，不表示能力已经实现；完成情况见 [tasks](tasks.md)。

## 1. 目标与边界

用户指定资料和对象，查看构件、观测、面积与关系，再点击 Evidence 回到对应 PDF 的页和区域。复用 S2.3 的真实图与已发布文件；本阶段查询不调用抽取模型、不重新转换 PDF、不重新生成素材。向量、问答、报告、人工审核写入不在本阶段。

主要规范：[根 spec §10](../../spec.md#10-切块检索与上下文)、[§14](../../spec.md#14-api工具与端口契约)、[§15](../../spec.md#15-网页与证据查看)；业务字段及身份见 [Spec 001](../001-business-extraction/contract.md)。本文仅补充阶段接口和验收，不复制业务 Schema。

## 2. 查询契约

`POST /api/graph/lookup`，请求包含：

- `query_type`：bridge_components / component_observations / defect_history / record_by_id。
- `scope`：沿用 SearchScope 的 bridge_id/null、component_ids[]、document_ids[]、date_from/null、date_to/null、review_mode=accepted/confirmed_only、include_synthetic=true。
- `record_id`：record_by_id 必填；`defect_id`：defect_history 必填。病害历史只按已确认的稳定 ID 查询，绝不用名称、相似文本或 PDF 年份合并。
- `acceptance_mode`：accepted（默认）/pending_review。pending 仅用于明确查看缺项，不作为默认业务事实；与 confirmed_only 同时选择 pending 属于非法组合。
- `area_min`/`area_max`：可空十进制字符串，单位固定 m²，仅用于观测查询；上下界包含等于，最小值非负且不大于最大值。不允许数值 JSON、NaN、Infinity 或浮点比较。

至少给出 document_ids 或 bridge_id；组件列表本身不替代资料/桥梁范围。component_observations 必须给组件列表；桥梁构件查询不接受日期/面积条件。record_by_id 若不是观测，不接受日期/面积条件。无范围返回 422 INVALID_REQUEST，网页提示选择资料或桥梁。

允许多文档或明确桥梁范围，源文档通过受支持证据关系限定；共享 Bridge/Component 的单个 source_document_id 不代替实际来源关系。来源 real/synthetic、节点状态和该来源的 SUPPORTED_BY 接纳状态均参与过滤。pending 查看允许全局已接纳但该来源待复核的共享节点；保留全局 acceptance_status 并另给 source_acceptance_status，网页显示该来源的状态，不能将其用于默认业务结果。confirmed_only 同时要求记录 review_status=confirmed；程序 accepted 不等于人工 confirmed。

有日期条件时未知 observed_at 排除，不补日期；边界是 ISO 日期 YYYY-MM-DD 且包含两端。无日期条件返回 null 并标为“日期未知”。日期范围不能拿上传时间过滤。

先按范围、对象、来源和状态读最多 501 个候选，再由应用层 Decimal/日期过滤；候选超 500 返回 409 RESULT_LIMIT_EXCEEDED，要求缩小范围，不给出截断的完整统计。结果按类型、稳定 ID 排序去重，每条携带类型、业务字段、evidence_ids、source_document_ids、source_kinds 及状态；area/observed_at/quality_score 等缺值保留 null。响应含 records、count、warnings、omitted_unknown_date_count、trace_id；日期过滤只统计当前许可候选中未知日期的排除数量，无日期条件为0，有遗漏时附warning；无符合项是成功空列表。未知 ID、范围外 ID 不泄露范围外内容，返回空列表。

只使用固定标签、固定关系和参数化查询。工具与未来其他入口复用同一应用用例，不允许任意 Cypher 请求字段。

## 3. 证据契约

`GET /api/evidence/{id}` 是明确引用回查，包括 pending 引用；不把回查权限理解为 accepted。返回 evidence、block_text、document（ID、文件名、source_kind）、records（类型、ID、该来源接纳状态、复核状态）、page_geometry、pdf_url、warnings、trace_id。不返回本地路径。

服务端核对图 Evidence、TextBlock 与已发布 normalized 原文、位置（沿既有六位小数精度，误差不超过 0.000001）和 source_ref，核对 quote/Unicode 码点 charspan；文件按既有 manifest/hash 校验。篡改、失配、缺文件返回明确错误，不定位到近似匹配文字。页面或区域缺失时仍可显示可靠文本，明确警告，不造框。

PDF bbox 沿用规范化的左上原点 [0,1] 四元组；缺失为 null。PDF.js 同源加载已验证 `/api/documents/{id}/file`，使用页 viewport 及几何旋转转换覆盖层，缩放、旋转和窗口变化重绘。错误/越界坐标不绘框。原文中只按服务器验证的 Unicode 码点 charspan 高亮，不用 JS UTF-16 偏移猜测。

## 4. 最小网页

`GET /query` 提供单页精确查询入口和证据面板。先填文档/桥梁范围，再选择查询类型、组件/病害/记录 ID及可选条件。每条结果显示来源、接纳/人工复核状态、未知项及 Evidence 按钮；缺面积 pending 与空结果区分。打开证据显示文件名、SYNTHETIC/真实、页码、原文高亮和 PDF 区域。支持缩放、90 度旋转和小窗口；原文通过 textContent 渲染，不执行 HTML。

PDF.js 主库、worker、字体和 CMap 均在项目同源提供，版本/许可证/摘要记录，不依赖运行时 CDN。保留本地单用户身份和应用分层；页面不是完整的资料录入、问答或报告产品界面。

## 5. 独立验收

人工预期来源始终是 [原件事实清单](../../samples/synthetic/bridge-demo-001/golden.json) 与 [受控变体预期](../../samples/synthetic/bridge-variants-001/expectations.json)；实现前固定查询预期于 preparation，不从查询输出生成正确答案。

1. 原件文档范围：1 桥、2 构件、3 观测；面积 0.04/2.6/0.9 m²；面积 ≥1 只返回 2.6，≤1 返回 0.04/0.9。原件日期限定返回零，未知日期保持 null。
2. 缺面积范围：默认 2 观测，pending 模式 1 条 O-002，面积及近似性 null；不得把原件 2.6 补过来。confirmed_only 当前为空，include_synthetic=false 当前为空。
3. 记录 ID 不绕过文档/桥梁/组件/来源/接纳过滤。关系查询对应实际固定关系；同名跨桥测试隔离。历史用明确 synthetic fixture 的同一 ID与两日期证明机制，不宣称真实检测历史。
4. >500 候选显式失败；Decimal 2.6/10.0 不按字符串排序；恶意类型、反向日期/面积、非法数值拒绝。证据缺失/失配/文件篡改不伪造成功。
5. 实际 Neo4j + TCP HTTP 精确查询、Evidence 与 PDF 成功，provider generation=0。浏览器真实渲染两页原文、高亮、缩放/旋转、浅深系统主题和小窗口；测试/agent 检视不等于用户专业人工验收。
6. 旧样本、抽取冻结和首次结果摘要不变；基线测试不回退，源码分层检查通过。失败原样留存，修复后另存结果。
