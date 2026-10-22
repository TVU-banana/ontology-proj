# Spec 007：S5.1 Agent 与完整网页

基线：2026-10-11，main / 498b105，干净工作区。用户授权先定案计划与独立预期再实现；本文件在实现前固定。

## 接入决定

沿用 FastAPI、同源原生 HTML/JS/CSS 和已配置中转 Chat Completions function tools；新增薄工具调度器与有界循环，不引入 Agent 框架。协议依据：[官方工具调用说明](https://api-docs.deepseek.com/guides/tool_calls/)。协议支持不代表本地中转能力，须实测。

信息录入入口 `/api/agents/ingestion`，接收已登记 document_id、instruction。服务器固定角色；仅 process_document/get_operation/get_ingestion_result。上传仍走原 HTTP；处理沿用原后台 runner。处理 key 由 run_id + 调用参数稳定生成，同轮重复写拒绝，最多一次状态读取，不轮询。

知识入口 `/api/agents/knowledge`，接收 instruction、scope。scope 先用原查询验证，服务端注入每个检索/问答/报告工具，模型不可覆盖范围、身份或权限。工具 graph_lookup、semantic_search、get_evidence、answer_question、generate_report；补充 answer_question 只是调用已核验 S3.3 用例，不新增业务语义。get_evidence 先检查引用对应记录/文档属于许可 scope，拒绝范围外引用。模型自由收尾文字仅入 trace，不作为业务答案展示；UI 展示 ToolResult 和已核验 Answer/Report。

最多8个模型轮次且最多8个工具调用；重复工具+参数拒绝并终止，非法工具/参数终止，无自动网络重试。工具调用前验证实际参数；模型/文档/工具内容不得提升权限。每轮保存 request/response，每个工具保存实际参数、request_id、结果/错误、耗时；运行 summary 落在 DATA_ROOT/agents/{run_id}/。传输错误保留，UI 可见 run_id。输入/模型输出/工具上下文有长度限制；不会剪掉字段后假装完整上下文。

网页 `/`：上传、文档/操作状态、显式处理/重试/索引、精确记录入口 `/query`、核验问答、报告创建/状态/重试/Markdown导出、两角色调用及工具记录。证据复用已有 `/evidence/{id}` PDF 查看器；不改抽取规则/Schema/历史。报告逐章/逐行文本显示，引用构造同源链接，不渲染不可信 HTML。明确合成来源、未知日期与记录复核状态。按2026-10-14用户要求，网页移除演示标语、评分/专业审核占位、内部追踪字段与诊断展开；底层状态和接口参数保留，范围输入与操作控件保留，标签采用用户文案。状态轮询只由网页发起并有上限，写操作不自动重发。

## 范围与验收边界

不新增专业评级/处治、跨期身份推断、审核工作台、外网认证、S5.2持续稳定性；不重跑解析/抽取/嵌入旧实验。新真实验收优先复用已登记素材，仅触发本步工具循环与既有读取用例。上传演示采用同一已登记PDF的重复上传，不重新抽取。无法获得真实provider/浏览器证据时明确未验，不把替身通过计为完成。

独立预期见 [expectations](expectations.md)，实施次序见 [plan](plan.md)，任务状态见 [tasks](tasks.md)。根边界见 [spec §12](../../spec.md#12-agent-运行和质量占位)、[§15](../../spec.md#15-网页与证据查看)。
