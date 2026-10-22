# 四份受控 PDF 变体

当前状态（2026-10-11）：四份两页、可选择文本的 synthetic PDF 已制作；101 项制作核验通过，8 页经 Poppler 130 dpi 渲染并由 agent 查看。随后用户授权第二步，四份 Docling 转换与 120 项解析核验已通过，8 页来源坐标经绘框查看；见 [解析验收](../../../experiments/docling-variants-001/results/review.md)。尚未运行这组变体的业务抽取或稳定性验收。

## 业务输入

| 文件 | 修改 |
|---|---|
| [missing-area.pdf](input/missing-area.pdf) | O-002 正文及表格均删除 2.6，明确保留 m² 单位 |
| [additional-negative.pdf](input/additional-negative.pdf) | 新增“第三跨主梁中部未见露筋。” |
| [duplicate-row.pdf](input/duplicate-row.pdf) | 表 3 原数据行后重复一次 D-001 / O-001 行 |
| [reordered-columns.pdf](input/reordered-columns.pdf) | 表 4 改为“观测编号 / 面积 / 位置 / 病害编号 / 数量” |

仅 `input/` 中的 PDF 作为录入资料；其余清单、生成器、核验与渲染材料都不作为模型输入。

## 预期与制作

- [预期清单](expected-facts.md)、[机器预期](expectations.json)与 [freeze.json](freeze.json)保留制作前固定的版本 1.0，未改业务真值或摘要。它们的“尚未生成/未执行”是冻结时快照，当前产物状态以本页及 [validation.json](validation.json)为准。
- [generate_pdfs.py](generate_pdfs.py)独立定义创作内容，复制原件的样式与未指定文字；不读取预期、golden 或模型输出，不改写原件或原生成器。
- [verify_pdfs.py](verify_pdfs.py)从原 PDF 独立提取文字与表格，按冻结 JSON 的修改操作推导对照；检查仅发生指定内容变化、页数、日期、编号、面积、字体与边界。
- [validation.md](validation.md)记录制作范围与视觉核验；[validation.json](validation.json)记录四份 PDF 的 SHA-256、大小和检查结果。

Docling 解析已完成；后续进行固定模型配置下每份独立抽取两次（加原件共十次）及结果对照。每一步按用户指令开展，本次未修改解析/抽取实验入口。
