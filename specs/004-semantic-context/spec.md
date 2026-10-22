# Spec 004：语义检索与受控上下文

日期：2026-10-11。范围仅为 [roadmap S3.2](../../docs/mvp-roadmap.md#s3完成检索证据回查和问答)，用户授权推进至收尾。入口：[计划](plan.md)、[任务](tasks.md)。模型服务已部署不等于本阶段完成；不实现 S3.3 回答生成。

## 1. 输入与边界

复用已发布 normalized/records、实际 Neo4j 与 [本地模型](../../deploy/embedding/README.md)，不再转换 PDF 或调用抽取模型。根规范 [§9.4–10](../../spec.md#94-向量索引) 保持有效。核心不依赖 SDK；新适配器只经端口调用。

业务片段从已校验的记录引用构建，每个片段只归属一个业务记录，不混入 pending。表格通过字段名称与对应原文单元格引文组织，保留每个单元格的跨度与证据；说明/否定/疑似从已保存 statements 构建 source_statement，保留 semantic_kind，不升级为实体或事实。未知专业分类统一 other。未关联的任意标题/页脚/装饰不索引。保留 excluded 数量及原因。

总片段最长 1200 字符；长引文窗口 1100 原文字符、重叠 150，预留字段上下文；窗口保留原块 charspan，跨块不猜坐标。一个片段可有多份来源跨度。原文位置不足的部分排除；标注字段名称是检索上下文，不是新增原文。

## 2. 模型与发布

原生 Ollama `/api/embed`，固定模型、内容摘要、1024 维、查询指令、归一化、4096 context 与切块版本形成 profile。调用前后检查模型摘要；`truncate:false`，超长报错，不静默截断。每次最多 16 个片段，向量数量/维度/有限性/非零均校验并规范化。token 上限由模型服务严格检查，不把字符数当 token 数。

Neo4j Community 的 DocumentChunk 普通浮点列表，cosine 向量索引；检查维度、标签、属性、相似度配置及 ONLINE。全库只接受一个 profile；配置改变明确冲突，不隐式更新索引。确定性 chunk_id 包含配置与原文/来源，事务发布该文档的完整一代片段及新原文 Evidence；旧业务对象及 Evidence 不覆盖。业务可见性查询时再核对当前节点及来源关系状态。

索引为独立同步用例，显式调用 `POST /api/documents/{id}/index`，与旧 process/retry 分开。按文档运行 token 防止并发发布；已 ready 且 profile 相同则无模型调用复用；running 不自动接管，显式 retry 处理失败/中断。index_status 独立 running/ready/failed，索引失败保留 graph_ready。进程中断不会自动嵌入，重试不重跑抽取。

## 3. 查询接口

- `POST /api/semantic/search`：`query`（1–2000 字符）、`scope`（复用根 SearchScope）、`top_k`（1–20，默认5）。禁止额外字段及任意 Cypher。必须指定文档或桥梁；多桥文档而未指定桥返回 needs_clarification。
- 先解析允许文档集合（最多100），再向量查最多200候选；文档、桥、构件、日期、来源与当前审核状态过滤后按分数排名。不越界补满；可不足 top_k。返回候选池上限警告，不承诺全库精确召回。
- business_fact 要求当前记录及来源关系均 accepted；confirmed_only 还要 confirmed。未知观测日期不进入日期限定结果。source_statement 保留许可文档与 component 限定，通用说明可补充但不是某日期事实；confirmed_only 不返回未经人工审核说明。
- 返回 chunk_id、text_for_embedding、content_kind、semantic_kind、statement_kind、record_ids、source_kind、source_spans、evidence_ids、score，同时返回当前 acceptance_status/source_acceptance_status/review_status；说明为 source_only/unreviewed，不返回 embedding。失败返回 degraded/error_code；精确图查询独立可用。
- `POST /api/context`：同上，可传 `graph_query`（既有受控图查询字段，不传 scope/acceptance_mode）。强制与语义查询同范围、accepted。精确事实优先，语义项保持跨分类相关性排名后按预算取舍；分别返回 facts/business_passages/source_statements，稳定 ID 去重，JSON 字符预算 8000；预算排除完整项并告警，不截断引文。无回答生成。

新片段的引文通过既有 `/api/evidence/{id}` 校验 published 原文及 PDF 哈希，可回查页码、坐标和 PDF。原始文字作为不可信资料，ContextBundle 仅是带来源数据，不执行资料中的指令。

## 4. 验收

独立 [预期](results/s3.2/preparation/expectations.json) 在实现和嵌入前固定，不发给模型。离线覆盖边界、纯核心导入、错误响应、配置漂移、状态/范围过滤和预算；实际本机 Ollama→Neo4j→HTTP 验证两份旧素材的索引与检索，所有返回引文回查。实际故障或隔离 fixture 验证 pending/confirmed/日期变化、索引失败不污染图、重试与复用；不改正式业务事实。

完成须记录真实索引 ONLINE、真实向量调用、可追溯范围内结果、独立预期通过与旧产物哈希保持。小 synthetic 通过不表示真实复杂报告、模型最优、用户专业审核或跨期病害身份已验证。

当前完成证据与限制见 [验收](results/s3.2/review.md)。
