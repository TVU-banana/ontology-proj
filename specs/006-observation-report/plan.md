# S4.1 计划

1. 先固定 [契约](spec.md)、[模板](template.md)、[独立预期](expectations.json) 与机器 Schema；同步根规范/ADR。
2. 核心复用 LookupGraph 和 EvidenceReader；适配器负责 Schema、原子文件、报告登记事务；接口复用应用用例，异步 runner 与报告专用恢复。
3. 检查查询上限、逐字段引用/原值/日期/限定、当前接纳；生成报告 JSON/Markdown，保存后事务登记；失败/显式重试/重启/后来撤回可见。
4. 离线测试非法范围、日期/pending/空数据、Markdown 注入、文件损坏、失败/重试；实际图事务与既有两来源真实文件/HTTP验收，0 模型调用。只新增报告文件/图登记和标注 fixture，保护旧产物。
5. 收尾保存首次失败/最终结果，核对旧文件摘要、链接和 ADR，更新 roadmap/README/交接。S5 不在本轮实现。
