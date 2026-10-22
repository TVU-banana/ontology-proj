# Agent 工作入口

本文件只维护稳定的阅读与更新规则。当前进度、待定项和下一步见 [.handoff/latest.md](.handoff/latest.md)，不要在这里复制项目百科或任务历史。

## 开始工作

1. 读取用户当次指令与 [.handoff/latest.md](.handoff/latest.md)，明确目标、范围和已有证据。
2. 检查 `git status --short`、当前分支及 HEAD；无提交时如实记录，保留已有修改和未跟踪文件。
3. 根据下表只读取当前任务相关章节。信息不足、跨模块依赖或发现冲突时，再扩大阅读范围；不默认通读 PRD、spec、历史交接或全部源码。

## 按任务读取

| 当前任务 | 优先入口 |
|---|---|
| 产品目标、范围或需求变更 | [PRD §1 接手摘要](PRD.md#1-接手摘要先读这一节)、[§3 范围](PRD.md#3-范围与优先级)、[§16 待确认项](PRD.md#16-待确认项与接手约束) |
| 选样、人工事实清单或解析验证 | [PRD §4.1 样本](PRD.md#41-pdf-素材)、[spec §6 文档与来源](spec.md#6-文档与来源数据契约)、[§18 测试数据](spec.md#18-测试数据与独立核验)；候选详情再读[讨论交接 §6](MVP范围与业务建模讨论交接.md#6-pdf-样本与核验边界) |
| 业务建模、身份、测量或抽取规则 | 桥梁身份与归属另见[Spec012计划](specs/012-bridge-identity/plan.md)、[任务](specs/012-bridge-identity/tasks.md)、[预期](specs/012-bridge-identity/expectations.md)；规则表达兼容另见[Spec011计划/契约](specs/011-business-rules/plan.md)、[任务](specs/011-business-rules/tasks.md)、[独立预期](specs/011-business-rules/expectations.md)；当前字段先读 [最小业务契约 §1–4](specs/001-business-extraction/contract.md#1-审阅顺序与产物)；总体边界见 [PRD §4.3](PRD.md#43-mvp-业务模型范围)、[spec §5](spec.md#5-共用契约与标识)、[§7](spec.md#7-业务数据契约)，涉及位置再读 §6；采用范围与确认状态以契约入口和用户最新指令为准 |
| 决策理由、技术选择或既有约束变更 | [ADR 索引与维护规则](docs/adr/README.md)，只展开当前任务相关记录 |
| 架构、依赖方向或适配器 | [spec §4 分层架构](spec.md#4-分层架构与代码组织)、[§14.3 核心端口](spec.md#143-核心端口) |
| OCR引擎、离线模型或自动路由 | [Spec013计划/契约](specs/013-ocr-routing/plan.md)、[任务](specs/013-ocr-routing/tasks.md)、[独立预期](specs/013-ocr-routing/expectations.md)、[结果](specs/013-ocr-routing/results/review.md)；选型理由见ADR-019 |
| 文档图片、照片说明或跨页续行 | [Spec 010计划/契约](specs/010-document-structure/plan.md)、[任务](specs/010-document-structure/tasks.md)、[独立预期](specs/010-document-structure/expectations.md)；文档主边界见spec §6.2及ADR-016 |
| 录入、持久化、状态或失败恢复 | [spec §8 文件](spec.md#8-文件布局与原子保存)、[§9 图存储](spec.md#9-neo4j-模型写入与索引)、[§11 录入流程](spec.md#11-资料录入流程与状态机)、[§16 错误与重试](spec.md#16-错误分类重试与失败可见性) |
| S3.1 精确查询与证据查看 | [Spec 003](specs/003-query-evidence/spec.md)、[plan](specs/003-query-evidence/plan.md)、[tasks](specs/003-query-evidence/tasks.md)；总体契约与位置按阶段文件链接读取 |
| S5.1 Agent 与完整网页 | [Spec 007](specs/007-agent-web/spec.md)、[plan](specs/007-agent-web/plan.md)、[tasks](specs/007-agent-web/tasks.md)、[独立预期](specs/007-agent-web/expectations.md)；边界按阶段链接读取 |
| S5.2 系统验收与可复现交付 | [Spec 008](specs/008-system-acceptance/spec.md)、[plan](specs/008-system-acceptance/plan.md)、[tasks](specs/008-system-acceptance/tasks.md)、[独立预期](specs/008-system-acceptance/expectations.md)、[验收矩阵](specs/008-system-acceptance/acceptance.md)；持续门槛以用户确认和阶段冻结为准 |
| 检索、问答或 Agent 工具 | [spec §10 检索](spec.md#10-切块检索与上下文)、[§12 Agent](spec.md#12-agent-运行和质量占位)、[§13.1–13.2 答案](spec.md#13-答案和报告契约)、[§14 接口](spec.md#14-api工具与端口契约) |
| 汇总报告或证据查看 | [PRD §7 报告范围](PRD.md#7-业务模块三可追溯报告生成)、[spec §13.3–13.4 报告](spec.md#133-reportrequest-和-reportdata)、[§15 查看器](spec.md#15-网页与证据查看)；位置约定见 §6 |
| 阶段计划、任务细化 | [MVP 落地清单](docs/mvp-roadmap.md)，选当前步骤及相关契约，不提前通读其他步骤设计 |
| 验证或验收 | [spec §19 测试计划](spec.md#19-测试计划)、[§20 验收追踪](spec.md#20-验收与-prd-追踪)，只选本次涉及的场景；阶段完成条件见[落地清单](docs/mvp-roadmap.md) |

## 文档职责与冲突处理

普通业务抽取（构件/部位、无观测编号、同行证据）另见[Spec014计划](specs/014-catalog-evidence/plan.md)、[任务](specs/014-catalog-evidence/tasks.md)、[独立预期](specs/014-catalog-evidence/expectations.md)。

- [PRD.md](PRD.md)：产品目标、范围及主要验收条件；[spec.md](spec.md)：当前设计、行为、契约及验证要求。技术默认与待定设计按 spec §1 区分，不当作逐项已批准或已实现。
- [docs/adr/README.md](docs/adr/README.md)：仍有效的决定、理由、适用边界与替代关系；不复制规范字段或进度。已接受不表示已实现/已验证；按任务定位，不通读全部历史。
- [docs/mvp-roadmap.md](docs/mvp-roadmap.md)：线性构建步骤、依赖与完成条件；各步骤的详细计划/任务按需建立，当前进度与证据由交接链接到具体产物。
- [MVP范围与业务建模讨论交接.md](MVP范围与业务建模讨论交接.md)：历史讨论、取舍理由与候选资料；仅追溯依据时读取。其“尚未同步”等状态描述不代表当前状态。
- 用户最新明确指令优先。文档中的历史授权记录不限制后续已授权工作，也不自动授予新工作权限。发现范围、契约或代码冲突时核查来源，未能确定则及时询问，不按旧文档扩大范围。
- 同一事实只有一个主要维护位置，其余文档使用摘要和链接。未来迁出架构、模型或契约时，同步原位置与本入口，不保留两份完整副本。

## 执行、验证与收尾

- 按已授权范围推进；区分需求、设计、代码实现、替身测试、真实服务运行与人工验收。样例、接口定义或模拟结果不能当作实际能力。
- 只运行与本次变更相关、实际存在的检查。文档改动核对链接、范围和契约引用；实现改动按 spec §19–20 选择验证，命令与证据以当前仓库为准。
- 影响范围时更新 PRD；影响设计、契约或验收时更新 spec；历史讨论保留原文。每次形成可核验结果或结束工作时更新 [.handoff/latest.md](.handoff/latest.md) 的状态、证据、未知项及下一步。
- 每轮改动检查 ADR 影响：范围、业务语义、架构边界、存储、关键技术选择或验收标准变更时按 [ADR 维护规则](docs/adr/README.md) 新增/替代记录，同步主要规范和索引；无决策变化不强制修改 ADR 正文。交接注明受影响编号或“ADR 检查：无决策变化”。普通修复和测试进度进入任务/交接；用户要求只读讨论时不强制写文件。
- 新增目录、文件改名或章节移动时检查受影响指针。默认中文，说明实际结果和限制；交接只保存恢复工作需要的信息，大段输出与历史按需引用。
