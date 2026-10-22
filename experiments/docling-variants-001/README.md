# Docling 四种受控变体解析

2026-10-11，按用户授权完成第二步。四份两页 synthetic PDF 各进行一次真实 Docling 转换，120 项解析核验通过、0 失败；294 个块的来源页码与坐标已绘框，8 页由 agent 查看。业务模型调用次数为 0。

结论与边界见 [解析验收记录](results/review.md)；逐项检查见 [verification.json](results/verification.json)。输入与预期见 [样本入口](../../samples/synthetic/bridge-variants-001/README.md)。

## 运行与产物

[parse_variants.py](parse_variants.py)导入原 [解析脚本](../docling-parse-001/parse_pdf.py)，仅为每份配置输入和隔离输出目录；不读取 golden/预期，不修改 Docling 或原实验。每份独立创建转换器，复用原 `.venv`、离线模型缓存与版本锁定：Docling 2.137.0、CPU / 4 threads、OCR 关闭、默认准确模式表格结构开启。

```bash
HF_HUB_OFFLINE=1 HF_HUB_CACHE=/path/to/user/.cache/huggingface/hub HF_HOME=/private/tmp/ontology-docling-hf experiments/docling-parse-001/.venv/bin/python experiments/docling-variants-001/parse_variants.py
```

转换目录必须不存在，避免覆盖已有运行。重跑需另建运行目录，不能删除或覆盖首次记录。

四个 case 目录分别保存 `docling.json`、`docling.md`、`run.json`。验收时 [normalize.py](normalize.py)沿用原结构适配逻辑，保存原块文字、来源引用、页码及归一左上角坐标；它不生成业务对象、不推断表头。`normalized.json` 仅实验适配产物。`evidence-checks.json` 是解析核验用原句切片，不是正式业务 Evidence。

[verify_results.py](verify_results.py)在转换完成后读取冻结预期，检查正文、四表、重复保留、列序、来源与坐标；以已验收原件作未变内容对照，同时逐条检查清单中的肯定/否定原句。每份先用 `pdftoppm -r 130 -png INPUT CASE_OUTPUT/page` 独立渲染原 PDF，再运行：

```bash
experiments/docling-parse-001/.venv/bin/python experiments/docling-variants-001/verify_results.py
```

核验脚本不会调用 Docling 或模型，重跑会将 visual_review 重置为 pending，需要重新查看对应定位图。

首次检查将能力集合 `supported_engines` 的序列化顺序差异误判为配置差异；保存于 `verification-initial.json`。现仅对该字段按集合含义比较，其他列表顺序与所有配置值严格比较，原 run.json 未改，Docling 未重跑。详见验收记录。

后续第三步仍需参数化抽取入口，固定当前成功实验的模型、Schema、提示词与参数；原件及四份变体各独立两次，共十次。此次不读取私有 API 配置、不修改抽取程序、不开展第三或第四步。
