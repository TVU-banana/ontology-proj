# Spec 006：S4.1 病害与观测汇总报告

## 1. 范围与选择

2026-10-11 用户确认：程序生成固定汇总；关注重点仅接受结构化范围，暂不理解自由文字。不调用生成/嵌入、解析/抽取或重建索引。固定模板 `bridge_inspection_v1` / `1.2`（旧1.0/1.1快照仍可读），不是法规检测报告。参见 [PRD §7](../../PRD.md#7-业务模块三可追溯报告生成)、[根契约](../../spec.md#133-reportrequest-和-reportdata)、[ADR-010](../../docs/adr/ADR-010-确定性汇总报告.md)。

## 2. 请求与数据

Request：bridge_id 必填；component_ids/document_ids 默认 []；date_from/date_to 默认 null；review_mode=accepted 或 confirmed_only；include_synthetic 默认 true；template_id 固定；expert_requirements 仅空字符串。非法字段、日期倒置、非空自由文字和其他模板均 422。document_ids 沿用精确查询范围，便于分开两份演示资料。专业审核不接受虚构通过状态。

ReportData 的机器定义由 [report.schema.json](../../src/ontology_proj/resources/report.schema.json) 唯一维护。顶层含 schema_version/report_id/template_id/template_version/generated_at/scope/source_document_ids/sections/missing_information/conflicts/warnings/checks/professional_review。五章固定顺序：scope、sources、observations、limitations、evidence_index。章节 claims 可为空；本阶段不生成自由结论。表格由程序构造，不能开放模型任意单元格。

观测行：row_id、component_id、defect_id、observation_id、record_ids、evidence_ids、field_evidence、values。values 保留 observation_code、observed_at、description、assertion、scope、reported_count、source_kind、area_original_value、area_original_unit、area_value、area_unit、area_conversion、area_is_approximate、missing_fields、review_status。面积为 Decimal 字符串/null，单位 m²；null 不补零，近似/否定/疑似及统计范围不丢失。每个非空业务字段须有对应 target_field 证据，规范面积须复算一致；直接面积用原值/单位证据，派生面积用完整area_expression证据。新版可选values增加area_expression/area_basis/area_derivation/area_rules_version，单位及派生边界以[业务契约§4.2](../001-business-extraction/contract.md#42-observation)为准。状态/来源为程序登记元数据，不冒充原文断言。

默认只汇总 accepted；pending 不放入事实表，缺失说明列明其数量，不自动接纳。日期过滤排除未知日期并列出数量，报告生成时间与观测日期分开。病害 ID 保持文档局部，不跨来源合并或推导总体病害数/总面积。空范围保留完整五章并说明无资料/无记录。

资料清单包含本次许可的已发布文档，构件过滤通过其证据范围收窄。原文说明仅使用 S3.2 已登记的 source_statement 片段（直接枚举，不向量搜索），逐条回查原句/来源；日期过滤或 confirmed_only 时未知日期/未人工确认的说明不纳入，明确警告。无已登记片段时提示缺少可引用原文说明，不新建证据。否定说明保持原文，不转换为肯定病害。冲突列表为空时注明未执行自动冲突判定，既有文档 conflict_ids 以警告展示，不替专家裁决。

## 3. 状态、保存与登记

POST /api/reports 返回 202 report_id/operation_id，单进程 runner 执行；GET /api/reports/{id} 返回状态、错误/ReportData 和当前引用状态；GET .../markdown 返回正式 Markdown；POST .../retry 仅复用已保存且校验通过的文件进行登记，不重新查询生成；POST .../professional-review 返回 501 REVIEW_NOT_IMPLEMENTED、not_run。

模板1.2（2026-10-13）新增原始表达、单位换算/尺寸派生来源显示；旧版不重渲染。模板 1.0 首轮结果保持，1.1 修正 pending 缺项计数并缩短表格引用标签；读取旧版按登记摘要验证，不用当前渲染器重写旧 Markdown。

文件：DATA_ROOT/reports/{UUID}/input.json、report.json、report.md、manifest.json。单文件原子保存、摘要校验；图登记前文件可存在，但未登记不得下载为成功报告。Report 节点保存任务范围、文件键/摘要和模板元数据；CITES、Operation 成功和 AuditEvent 同事务提交。重复 retry 无第二份 Report/成功 AuditEvent。生成与登记前分别核验当前记录、接纳和字段证据；登记事务对当前引用记录加锁再比较快照，防止撤回后发布。重启将未完成报告任务标 interrupted；启动图不可用时保留未成功恢复标记，后续get/submit/retry须先补做恢复，未成功前不创建新任务；恢复成功后本进程不再次执行全局恢复，避免中断当前队列；有完整文件可显式重试登记，无完整文件提示新建任务，不自动重新生成。

已发布报告快照不因后来撤回修改。GET 返回 citation_status_changes，Markdown 获取也重新检查来源文件；引用撤回时通过响应状态提示，旧报告仍保留。图登记失败可重试；损坏文件/缺来源/字段变化不得发布。图操作状态是发布依据，文件存在不是成功。复用既有应用单写者租约；不承诺多进程/多 DATA_ROOT 共写安全。

## 4. 模板与检查

[template.md](template.md) 是人可审阅的布局规范。报告 Markdown 转义 HTML、表格控制符和 Markdown 注入；程序生成 `/evidence/{id}` 稳定查看链接及文档/页码/quote。checks 的 schema/references/artifacts/assertions 均须 passed，professional_review 永远 not_run。有限字段核验不等于完整语义或专业审核。引用查看路由复用 /query 查看器。

## 5. 验收

实施前固定 [独立预期](expectations.json)，来源是原 [人工事实清单](../../samples/synthetic/bridge-demo-001/expected-facts.md)和 [变体预期](../../samples/synthetic/bridge-variants-001/expected-facts.md)，不从待测报告回填真值。验证对应根 spec UT-09/12/13/15、IT-11/12/13、E2E-04/08、AC-10/11/12；真实图、真实文件/HTTP与替身故障分开记录。复杂真实 PDF、专业人工验收与 S5.2 稳定性仍未完成。
