# S5.2 实现前独立预期

2026-10-12 固定。数值来源为旧人工清单和已确认契约，非本阶段服务响应；用户已授权60分钟/120轮及5问答/两角色各2次，执行前冻结机器配置。

1. 原件 B-DEMO-001：2 构件、3 病害、3 观测；O-001/O-002/O-003 面积分别 0.04/2.6/0.9 m²，日期全部未知，source_kind=synthetic。桥头“未见明显病害”保留否定原文，不创建第四条观测。
2. 缺面积：2 accepted 观测、1 pending 观测，缺值 null，不能补零。原件和变体是两个文档局部来源，不是两次实际检测。
3. 排除 synthetic、人工 confirmed 筛选结果为空；日期过滤不得将编制、上传或当前日期补为观测日期。
4. 来源 PDF SHA、统一文档/业务发布摘要与旧快照一致；每条 quote 等于 charspan 对应切片，页/bbox 引用按既有规范核对。专业逐页核验仍未运行。
5. 固定报告 scope/sources/observations/limitations/evidence_index 五章；三个面积与独立预期一致，synthetic/未知日期/原值/限定可见，professional_review=not_run。重试复用同一报告/字节，成功审计仅一次。
6. 精确图读取不依赖生成或嵌入服务；语义不可用时有警告且保留图事实。没有模型时问答不得伪造 answered。重新可用后需要明确新请求，不自动重试旧模型请求。
7. 应用/数据库重启后，已提交文件、报告和引用可读；原业务事实不变；未完成任务如实 interrupted，不自动解析/抽取。故障恢复成功不能由文件存在推断。
8. 两角色共享权限与范围约束；知识角色用真实 graph_lookup 返回 2 构件；录入角色只读已完成状态，禁止再次 process。自由模型收尾不是业务真值。
9. 五次新问答建议覆盖构件、面积、未知日期、否定、专业结论不足；每次首次结果必须符合已确认有限断言/原句核验，不能发送预期答案。最终问句将在真实调用前单独冻结。
10. 旧实验、freeze、golden、原 DATA_ROOT 所有非私有配置文件摘要不变；原图读取摘要不变。UI 四文件及其既有证据也不由本轮修改。

依据：[原件清单](../../samples/synthetic/bridge-demo-001/expected-facts.md)、[受控变体](../../samples/synthetic/bridge-variants-001/expected-facts.md)、[报告独立预期](../006-observation-report/expectations.json)、[S5.1 预期](../007-agent-web/expectations.md)。synthetic 多日期/跨文档机制用例若新增，必须先另建显式人工预期，不从系统输出推导身份与日期。
