# S5.2 交付与接手入口

本阶段保留完整验收门槛。用户已授权[系统执行基线](execution.md)，[本轮实验记录](results/system-20261012-01/review.md)区分新连续运行/故障/真实provider与旧准备证据；不能宣称 MVP 全部验收。见 [范围](spec.md)、[计划](plan.md)、[任务](tasks.md)、[独立预期](expectations.md)、[AC 矩阵](acceptance.md)。结果保存到新的 `results/<run-id>/`；旧阶段目录不重跑或覆盖。

2026-10-12 既有 UI 已单独归档为 `7a45854`，S5.1 已 [确认收尾](../007-agent-web/closure.md)；继续 [真实素材验收](real-acceptance-plan.md)，用户已选胜利大桥报告，处理/外发范围与人工核查尚待确定。以下工作区/候选包版本说明属于已结束的历史试验，不能追认新 HEAD 的整套包验收。

随后用户明确授权本地查看/解析、暂不允许模型外发、本人专业核查。已补 [一次真实本地解析](results/local-shengli-20261012-01/review.md)及待确认草稿；原资料/全文/页面图/Docling及统一JSON在新忽略DATA_ROOT，本轮没有图、HTTP或provider调用。下一步专业确认草稿与映射/质量门槛；OCR、真实三模块及完整Done仍未完成。

用户现已 [确认 F01–F12 事实基线](results/human-baseline-20261012-01/review.md)，腹板/挑臂类型保持null，F03–F05为首轮核查范围；原草稿不改、另存已确认golden。接下来整理执行预期/门槛，外部模型仍不授权；实际系统结果和位置框尚未验收。

本轮小时系统试验使用含既有未提交UI的工作区，UI由专门会话处理；S5.2提交不混入其四文件/证据目录。另从已提交源码构建交付候选包（已提交UI版本），修复干净构建的PDF.js重复入包；源码/根配置相等与已安装包HTTP均通过。两种UI版本分开记录，不宣称提交可字节复现旧工作区wheel；专业人工/真实复杂素材完整验收仍未完成。

## 当前系统证据与恢复

[系统验收](results/system-20261012-01/review.md)：3,603秒/121轮/0普通失败，10正常报告、3应用实例重建与1专用图重启，另3新OS进程持久性177项通过；新provider5问+4角色首次9/9，13正常生成/7本地查询嵌入、0解析/抽取。全部首次辅助/构建失败保留。

本轮应用18100与专用Neo4j结束后停止，保留`data/s52-system-20261012-01/`及独立卷`ontology-s52-20261012-01-data`；旧图17687与共享Ollama未停止。若只需查看本轮已提交资料，确认18100和DATA_ROOT无应用进程后按顺序：

```bash
docker start ontology-s52-20261012-01
.venv/bin/python -m ontology_proj --env-file data/s52-system-20261012-01/private/.env.app
```

此配置仅本机忽略的私有文件，换机器从模板配置；不能从Git恢复密码/历史卷。查看不点process/index/问答/Agent生成或报告创建，既有结果无需重跑。`tools/prepare_system.py initialize`与执行器均用于一次新基线，旧目录/既有result直接拒绝；未来另建运行ID/独立基线，不直接执行`results/frozen-harness/`审查副本。

## 在另一台机器安装

需要 uv、Python 3.12 和 Docker；锁定 Python 依赖见 [uv.lock](../../uv.lock)，Neo4j 镜像固定摘要见 [compose](../../deploy/compose.yaml)。依赖获取需要网络或已填充缓存，不承诺完全离线安装；本轮离线首次安装因缺 pytest 缓存失败，原日志保留在 [install-01](results/delivery-20261012-01/install-01.txt)。具体安装验证见 [delivery review](results/delivery-20261012-01/review.md)。

```bash
uv sync --frozen
cp .env.example .env
cp deploy/.env.example deploy/.env.neo4j
```

分别在两个忽略的配置文件填入图密码，设置同一数据库鉴权。应用配置应明确 `DATA_ROOT`、Neo4j URI/用户/数据库、LLM 地址/模型/key、Docling 本地模型目录，以及嵌入地址/模型/digest/dimension。模板入口：[应用模板](../../.env.example)、[图模板](../../deploy/.env.example)。不复制本机私有 `.env.s32` 到 Git。

Docling 本地模型目录与初始化限制见 [既有配置核验](../002-ingestion-foundation/results/s2.1/t02-review.md)，共享嵌入部署见 [部署入口](../../deploy/embedding/README.md)。依赖安装不包含 Docling/Ollama 权重，也不证明任意 PDF/OCR 已支持。换机器必须复核可用模型/实际摘要，不能照抄本机状态。

## 启动和停止

先确认目标端口、DATA_ROOT 和图均不被另一应用写入。一个 DATA_ROOT 一个应用；不同 DATA_ROOT 不得同时写同一图。专用隔离验收环境需要不同容器、卷和端口，不能通过改 DATA_ROOT 复用现有图。

```bash
docker compose -f deploy/compose.yaml up -d
uv run --frozen python -m ontology_proj --env-file .env
```

应用默认 loopback 8000；如冲突在 `.env` 设 `APP_PORT`（本机旧演示使用18000）。检查 `/health` 与 `/ready` 返回的分项状态；ready 是图/文件基础可用，不等于所有生成/索引/解析能力已验收。启动不自动重跑解析/生成；未完成任务应标记 interrupted，由操作者检查后显式 retry。

应用在前台 Ctrl-C 停止，确认退出后才能重启同 DATA_ROOT。专用图按需 `docker compose -f deploy/compose.yaml stop`；保留命名卷，不执行 `down -v`。已有旧实验和发布产物均应保留。

## 演示路径和边界

使用 [synthetic 原件](../../samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf)，先阅读 [独立事实](../../samples/synthetic/bridge-demo-001/expected-facts.md)，仅 `input/` 的 PDF 作为录入输入；不上传 golden 或预期清单。

1. 打开 `/` 工作台，明确 source_kind=synthetic、bridge_id=B-DEMO-001，上传原件并记录 document_id。首次新环境处理会触发真实解析/模型调用，须先确认目的地与载荷授权、配置和调用成本边界；已有成功资料重复上传后不要再次点击 process/index。
2. 按 document_id 查询状态；精确构件应为2，观测为3；面积0.04/2.6/0.9m²，观测日期未知。分流只是 demo 策略，professional_review=not_run。
3. 在同文档范围内问答；点击引用查原 PDF/quote。知识角色自由收尾不等于已核验答案；只有共享问答用例通过验证的业务答案才能呈现为事实。
4. 生成 `bridge_inspection_v1` 确定性报告，读取五章、synthetic/未知日期/否定/原值/缺项，下载 Markdown并打开 `/evidence/{id}`。Markdown 的证据链接依赖应用路由，不是完整离线证据包。
5. 停止应用、确认退出、重启后读取原 PDF/统一 JSON/已提交报告；不点 retry 以制造新模型结果。失败、引用撤回与状态变化如实展示。

这些是操作说明；既有完整网页实际运行证据见 [S5.1](../007-agent-web/results/review.md)，不是本轮新机端到端运行证明。S5.2 持续/故障矩阵及新模型样例须以本阶段新证据评价，不能照此步骤把旧证据重计。

## 已知限制与未验证

- 当前只有本机单用户、synthetic 演示证据；复杂真实 PDF/OCR、真实历史观测、专业人工逐页/逐条核查未验收。完整 Done 缺口不因有清单而豁免。
- 文档局部病害不跨文档自动融合；原件/缺面积变体不是两次检测；生成、上传、报告年份不能替代观测日期。
- 外部模型具体 revision 未锁定；超时或中断后计费/上游结果可能未知；显式新请求可能再次收费，不保证跨资源恰好一次。
- 文件、数据库、模型分别失败；图基础可用不等于语义/生成可用。不执行自动模型重试、专家评分或结构化评级/处治章节。
- 源码/锁定依赖/配置模板/图约束和素材随仓库交付；私有凭据、权重与旧 DATA_ROOT 不纳入 Git，跨机复原历史运行数据需要另行授权备份，不凭源码伪造历史。

## 当前真实子集交付

[胜利大桥实际链路](results/real-shengli-revalidated-20261012-01/review.md)/[集中专业终审](results/real-shengli-revalidated-20261012-01/human-review.md)覆盖三观测、5问和固定报告。新wheel278源码一致，安装包导入复用本机依赖；不冒充跨机完整安装。新增受控配置见根.env.example；旧启动说明仍适用。完整S5.2等待专业裁定，OCR/跨页等不支持区域如实解释，非所有格式成功。

## 最终验收状态

用户已查看并反馈无问题，Agent再次核原页/输出后按原完整Done裁定S5.2完成，见[最终记录](results/final-review-20261012-01/review.md)。前述待终审状态属于历史；已解释的OCR/表格失败区域、真实历史与版本边界继续保留，不承诺任意真实PDF全覆盖。
