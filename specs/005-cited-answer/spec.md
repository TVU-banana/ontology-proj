# Spec 005：有引用的知识问答

2026-10-11；用户授权执行 roadmap S3.3。先落契约/预期，再实现和实际验证。范围不含报告或自主 Agent。

## 1. 请求与上下文

`POST /api/answers`：question（1–2000字符）、scope（既有 SearchScope）、top_k（1–20，默认5）、可选 graph_query（既有受控查询，不得覆盖 scope/acceptance_mode）。服务端调用 Spec 004 ContextBundle；语义返回的业务记录 ID 经同范围固定 record_by_id 重新读取，最多10个记录。投影可用字段并配套真实 Evidence 原文，JSON 输入预算24000字符，按完整记录/说明排除并告警；最终封装含问题后若仍超过24000字符，报 MODEL_CONTEXT_TOO_LARGE，不静默截断。纯核心不读文件/网络。

回答使用的许可记录、Evidence 与原文跨度来自本次受控上下文；不得引用库中未提供 ID。PDF/解析哈希、引文、当前记录与来源关系由既有查询/证据读接口核验。date/review/source/component 范围复用现有规则。说明保留 negative/unmapped/other，不升为检测事实。

## 2. 生成与核验

沿现有 OpenAI-compatible 中转，独立回答 prompt；一次 chat/completions 请求，无自动重试/修正，不改变抽取提示、配置或业务 Schema。原上下文与资料均为不可信数据，不执行其中指令。模型仅输出结构化提案：status、claims、reason_codes；每个 Claim 提案包含 kind、record_ids、evidence_ids、assertions，禁止任意 text 或 Cypher。

fact/summary 使用明确字段断言：record_id/field/value/unit；字段来自业务记录白名单，日期/数值/单位与当前字段可比较，接纳和来源关系均 accepted。数值 Decimal 等值比较，最终值由服务器取记录中的规范值。每个字段须有同记录对应字段 Evidence；规范面积指向 area_original_value 证据并配套单位证据。未知值不能变成具体断言；近似性与原文 assertion/scope 随面积自动呈现。

source_statement 不带数值断言，仅选择 source_statement Evidence，服务端逐字呈现引文并标记原文/语义类别。summary 仅为经过同样字段校验的列表总结，不生成新指标、评级或推断。最终 Claim.text 由程序按校验字段或原句渲染；不声称能验证任意自由文本的语义蕴含。

引用须属于本次许可上下文，并核对记录与 Evidence 的配对、target_field、来源、当前状态及原文。生成后再次读取引用/记录核验，防止使用已撤销接纳的内容。任一非法提案整份拒绝，不发布部分未验证回答。

## 3. 返回与记录

Answer 复用根 spec §13：schema_version=1.0、question_id、status、scope、claims、missing_information、conflicts、warnings、retrieval；claim_id由程序生成，assertions只保留已校验值，synthetic与未经人工复核警告显式保留。

answered 至少一个合法 Claim；insufficient_evidence/needs_clarification 无 Claim，使用固定 reason_codes 映射提示；首版不产生 conflicting_evidence；尚未实现跨文档冲突判断，相关问题应资料不足或要求澄清，不能把不同位置观测说成冲突。

无上下文/多桥待澄清不调用生成。模型不可用、截断、非法 JSON、未知引用/不匹配断言返回明确错误，不伪装为资料不足；语义 degraded 但有图事实时可以继续并带警告。

每次 question_id 在 DATA_ROOT/answers/{question_id}/ 保存输入、上下文、无凭据请求、原响应/提案、校验结果和最终 Answer。检索/生成错误也留存并返回 question_id；模型传输失败503、输出截断/格式失败502、断言/引用核验拒绝409。错误也留存；独立问答不写业务图、不修改旧文件。运行日志不作为自主 Agent 调用证据。

## 4. 验收

独立预期先固定：面积0.04与单位、构件关系、桥头否定、日期未知、专业结论不足、范围外资料不足。真实本机嵌入/Neo4j/中转/TCP 验证；替身覆盖错值/错单位/错记录引用、未许可合法 ID、原文转事实、提示注入、预算、模型失败/截断和当前接纳撤回。旧文件/业务图保持；失败记录不覆盖。通过小 synthetic 不表示复杂真实资料、自由措辞或专业人工验收已通过。

当前完成证据见 [验收](results/review.md)。
