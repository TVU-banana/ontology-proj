# Spec 004 实施计划

按 [spec](spec.md) 和 [tasks](tasks.md) 线性推进。

1. 固定旧实验/样本/契约和抽取资源哈希，写独立问题及应命中原文，核对两份正式素材。仅调用嵌入服务，不抽取或转换。
2. 普通 EmbeddingProvider / SemanticRepository / PublishedInput 端口；纯切块、可见性、输入/向量校验、索引与检索/ContextBundle 用例。
3. Ollama HTTP 适配独立地址、内容摘要及超长拒绝；Neo4j 适配固定参数化索引/事务/向量查询/运行 token 和动态状态。服务只有显式 index 可写片段，不修改旧录入 runner。
4. bootstrap/HTTP 接通 index/search/context；就绪探测真实模型/索引状态，不凭配置报告 ready。现有精确接口与客户端保留。
5. 离线测试，实际两份素材 index/search/evidence/context；独立临时 fixture 只验证机制，清理自己新增节点。正式文档只新增索引状态/片段及说明证据，不重写业务图。真实 TCP 验证与重启复用，保留首轮失败。
6. 检查保护文件/链接/凭据、任务和 roadmap 状态，更新 ADR/README/交接。实际运行报告放 results/s3.2；模型/图保持运行，临时 HTTP 进程结束后停止。

失败不改独立预期来迁就输出；修复后新结果文件。完成本阶段后下一步 S3.3，不顺带生成问答。
