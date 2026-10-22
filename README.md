# 桥梁检测知识库

面向桥梁检测资料的本机知识库：录入 PDF，抽取构件、病害和观测，按原文证据查询、问答并生成可追溯报告。

实现使用 Python 3.12、FastAPI、Docling、Neo4j 和同源网页；生成模型通过 OpenAI 兼容接口接入，语义检索可配置独立的 Ollama 嵌入服务。

## 主要能力

- 资料录入：来源保存、重复检测、本地解析、表格与位置保留、可配置 OCR、失败诊断和显式重试。
- 业务抽取：桥梁身份与归属、构件和部位区分、原文引文验证、面积规则及缺项保留。
- 查询与问答：精确图查询、语义检索、受控上下文、有引用答案、PDF 原页定位。
- 汇总报告：确定性观测汇总、证据链接及 Markdown 下载；不代替专业评级和审核。
- Agent 工作台：资料与知识两类角色复用应用用例，业务写入仍经过确定性校验。

已完成本机合成样本及部分真实资料验证。复杂表格、跨页结构、OCR 和模型输出仍有范围限制；合成测试通过不表示任意真实 PDF 都可正确处理。业务契约见 [spec.md](spec.md) 与 [最小业务契约](specs/001-business-extraction/contract.md)。

## 本机安装与启动

需要 Python 3.12、uv；图服务使用 Docker。Docling/OCR 和嵌入权重须单独准备，不包含在源码或 Python 依赖中。

```bash
uv sync --frozen
cp .env.example .env
cp deploy/.env.example deploy/.env.neo4j
```

在两个本地配置文件中填入同一个新生成的 Neo4j 密码；只修改私有文件，保留示例模板中的空值与占位符。

| 应用 `.env` 配置 | 用途 |
|---|---|
| `NEO4J_URI=bolt://127.0.0.1:17687`、`NEO4J_USER=neo4j`、`NEO4J_PASSWORD` | 连接仓库 Compose 提供的本机图服务 |
| `DOCLING_ARTIFACTS_PATH` | 显式本地模型目录；缺文件时不在运行中自动下载 |
| `DOCLING_OCR_MODE`、`DOCLING_OCR_MODELS_PATH` | 默认自动路由；需准备固定 OCR 权重，或显式选择 `off` |
| `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` | 自行选择并授权的模型提供方；使用中转时内容可能被转发 |
| `LLM_FALLBACK_MODELS` | 同一提供方的顺序回退列表；设为 `[]` 可关闭默认备用模型 |
| `EMBEDDING_PROVIDER`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL` 等 | 语义检索使用的独立嵌入配置 |
| `DATA_ROOT` | 本地资料和运行产物目录，默认 `./data` |

配置完整字段见 [`.env.example`](.env.example)，OCR 约定见 [Spec013](specs/013-ocr-routing/plan.md)，嵌入配置见 [部署说明](deploy/embedding/README.md)。

```bash
docker compose -f deploy/compose.yaml up -d
uv run --frozen python -m ontology_proj --env-file .env
```

应用显式加载 `.env`，不会隐式读取实验配置。默认地址为 `http://127.0.0.1:8000`，工作台 `/`、查询页 `/query`、API 文档 `/docs`。

```bash
curl --fail http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
```

`/health` 表示进程存活，`/ready` 提供分项就绪情况；就绪不等于完成实际模型生成或解析验收。
首次处理、索引、问答会使用所配置的模型服务，执行前确认数据目的地与费用。一个 `DATA_ROOT` 只运行一个应用进程，不同目录也不得并发写同一业务图。

应用按 Ctrl-C 停止；图服务可用 `docker compose -f deploy/compose.yaml stop` 停止并保留卷。不要通过删除卷来重置已有资料。

## 样本和测试

合成输入位于 [samples/synthetic](samples/synthetic/bridge-demo-001/expected-facts.md)，与真实资料分开。新报告先在工作台选择或登记桥梁，再上传处理，不能套用历史案例的固定身份。

```bash
uv run --frozen pytest -q
python3 -m unittest discover -s tests/security -v
```

离线测试使用已审阅的合成快照和替身。真实图测试需要显式配置专用测试数据库，否则跳过；本机测试不会证明公网服务安全。

本地 CLI 支持准备、解析、抽取和读取；参数与全局配置顺序可查看：

```bash
uv run --frozen python -m ontology_proj.local_cli --help
```

## 项目结构

| 路径 | 职责 |
|---|---|
| `src/ontology_proj/domain/` | 业务规则、身份和证据语义 |
| `src/ontology_proj/application/` | 应用用例、DTO 与端口 |
| `src/ontology_proj/adapters/` | 文件、解析、模型、图和索引适配器 |
| `src/ontology_proj/interfaces/` | HTTP、CLI 和同源网页 |
| `tests/` | 单元、HTTP、显式真实图和发布边界测试 |
| `specs/`、`docs/adr/` | 分阶段契约、计划和决策依据 |
| `samples/synthetic/` | 合成输入与独立预期 |
| `deploy/`、`scripts/` | 本机服务配置和检查工具 |

开发入口见 [AGENTS.md](AGENTS.md)，范围见 [PRD.md](PRD.md)，阶段导航见 [落地清单](docs/mvp-roadmap.md)。

## 安全与发布范围

当前应用用于本机单用户运行，未提供公网所需的用户鉴权、租户隔离和完整限流。保持应用和数据库绑定回环地址；GitHub 发布源码不表示服务已对公网部署。

API Key、密码、真实资料、日志、原始模型响应和本地模型均不随公开源码发布。公开版本从独立的干净 Git 历史导出，保留必需的合成测试夹具；历史文档中的部分原始证据链接仅在本地归档可用。

发布规则、持续密钥扫描与后续更新方式见 [GitHub 发布说明](docs/github-publication.md)，配置和凭据处理见 [SECURITY.md](SECURITY.md)。
