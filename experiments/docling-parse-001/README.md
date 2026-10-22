# Docling 单样本解析实验

范围：使用未经源码修改的 Docling 解析 [bridge-demo-001](../../samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf)，对照制作前固定的 [预期事实清单](../../samples/synthetic/bridge-demo-001/expected-facts.md)。用户已授权运行本实验；预期清单仍为 agent 编写、待人工审核。本实验不建立业务实例、调用业务抽取模型或写入 Neo4j。

## 预先确定的验收条件

1. 转换成功、两页完整，桥梁和两个构件的名称/编号保留。
2. 三条肯定描述原句可检索；位置与对应面积保留，单位 m² 不丢失。
3. 桥头“未见明显病害”与观测日期未知说明完整保留。这里只验证文字，不宣称 Docling 理解业务语义。
4. 四张业务表格有正确行列；三处病害/观测的编号、位置、数量和面积不串行，单位保留在面积单元格中；病害类型及构件上下文在邻近表题/正文保留。
5. 关键内容顺序与原 PDF 一致，正文和表格重复描述仍保留，去重属于后续业务层。
6. 关键正文/表格单元格有真实来源引用、页码及可验证坐标；归一化后可在原页绘框回查。

只读取 input/ 中的 PDF。解析脚本不读取 golden 或来源改编说明；独立核验脚本在转换完成后才读取 golden。缺失或匹配不唯一明确记录，不以清单修补解析结果。

## 环境与运行

Python 3.12.14、macOS arm64，独立 `.venv/`。完整版本锁定见 [requirements.lock.txt](requirements.lock.txt)。关闭 OCR（只验证文字 PDF），开启默认准确模式表格识别，CPU / 4 threads；完整配置保存于 results/run.json。Docling 源码没有改动。OCR、扫描、跨页复杂表格及真实报告适用性未测试。

```bash
UV_CACHE_DIR=/private/tmp/ontology-docling-uv-cache uv venv --python /path/to/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 experiments/docling-parse-001/.venv
UV_CACHE_DIR=/private/tmp/ontology-docling-uv-cache uv pip install --python experiments/docling-parse-001/.venv/bin/python -r experiments/docling-parse-001/requirements.lock.txt
HF_HUB_OFFLINE=1 HF_HUB_CACHE=/path/to/user/.cache/huggingface/hub HF_HOME=/private/tmp/ontology-docling-hf experiments/docling-parse-001/.venv/bin/python experiments/docling-parse-001/parse_pdf.py
```

最后一条命令依赖本机现有模型缓存；在其他机器上需先下载相应模型。实验最终记录会说明实际使用的模型修订与运行结果。

解析后核验（核验脚本不重新运行 Docling）：

```bash
pdftoppm -r 130 -png samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf experiments/docling-parse-001/results/page
experiments/docling-parse-001/.venv/bin/python experiments/docling-parse-001/verify_result.py
```

## 已完成结果

真实转换成功，约 12.50 秒；24 项自动检查通过，72 个块的归一坐标绘框逐页核对通过。表头标记和 caption 引用有缺口，两段长文连接处有空格；未修改 Docling 源码，不宣称业务抽取或真实报告能力。详细结果与两页定位图见 [验收记录](results/review.md)，等待用户审阅。核验脚本重跑会将 visual_review 重置为 pending，需重新查看当次位置图。

## 资料与分层边界

- [Docling 官方基本用法](https://docling-project.github.io/docling/usage/)（2026-10-10 已读取；web 工具受 HTTP 429 限制，改用 curl 读取官方网页）。
- 文档契约引用 [spec §6](../../spec.md#6-文档与来源数据契约)，独立核验引用 [§18](../../spec.md#18-测试数据与独立核验)。
- `docling.json` 是工具原生输出；统一块和位置是实验适配输出，不能伪称 Docling 原生业务 Schema 或正式应用实现。
