# Spec 003 实施计划

## 1. 阅读顺序

先读 [spec](spec.md)，再按 [tasks](tasks.md)执行。既有业务/录入契约仅按链接定位。本轮用户授权先写阶段文件并准备，再落代码；本计划范围只到 S3.1。

## 2. 前期准备

- 核对 Git/HEAD，保留 T06/S2.2/S2.3 的全部未提交改动，不整理提交。
- 固定旧 samples/experiments/Spec 001/Spec 002 results/抽取 resources 哈希；离线测试建立现状基线。
- 查询已有实际 Neo4j 和两文档已发布文件，检查图/文件来源关系；读配置但不打印凭据。
- 写独立查询预期；不会调用 LLM、Docling conversion 或增加正式业务数据。
- PDF.js 版本及本地资源在实现前固定。参考 [官方 viewport 示例](https://mozilla.github.io/pdf.js/examples/)，检查 worker 同版本、许可证及同源字体依赖。

## 3. 分层实现

- application 查询用例和 QueryRepository/EvidenceReader 普通协议：输入校验、日期/Decimal过滤、返回空值与限定来源；只依赖 stdlib/domain。
- Neo4j adapter：固定标签/关系、参数化范围与状态、候选上限。读操作不取得写入流水线锁，不重用包含全部证据的验收查询作为正式接口。
- evidence adapter：复用现有 IngestionStore 已发布读取和 source/PDF hash 校验；图原文与 normalized 对照。所有 SDK/文件 IO 在适配层。
- bootstrap 共享已有 graph/store，HTTP 仅调用应用用例。新增 graph lookup 和 evidence 路由，保留原上传/处理接口行为。
- 同源静态单页 HTML/CSS/JS + PDF.js；接口层可接收 bootstrap 提供的静态目录，不向核心塞文件路径，不引入前端框架或运行时 CDN。

## 4. 实施与验证顺序

T01 文档、基线、独立预期 → T02 查询核心与图适配 → T03 Evidence校验/API → T04 本地PDF.js网页 → T05 离线/真实图/TCP/浏览器验收及收尾。

离线测试覆盖范围与状态绕过、Unicode/坐标、Decimal/日期/500上限和错误；图 fixture 显式 synthetic，独立命名，仅清理自己创建的节点。现有两文档只读验收，任何临时进程结束后停止，不停止其他服务。需要附加日期机制 fixture 时不把其当新真实检测。

新增结果保存 `results/`，失败不覆盖。最后核对旧哈希、凭据泄漏、链接、边界测试；更新 roadmap/README/handoff 和任务状态。ADR 检查：既有 fixed query/PDF.js/Decimal 选择不另立重复 ADR；若引入实质新的语义/技术决定，先记录并同步规范。

## 5. 完成与限制

T05 有真实 HTTP/Neo4j 和实际浏览器证据后才能勾选 S3.1。环境缺失或无法做视觉核查则明确留下未完成项，不能用 TestClient/PNG代替实际浏览器。没有跨文档病害身份的真实素材，不宣称完整真实历史能力；没有用户逐条专业审核，不把自动 accepted 表示为 confirmed。
