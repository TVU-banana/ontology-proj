# 首份演示样本制作核验

日期：2026-10-10（Asia/Shanghai）。结果：制作与产物核对完成；**等待用户人工评审**。

## 文件与顺序

- PDF：[bridge-demo-001.pdf](input/bridge-demo-001.pdf)，2 页 A4，80,024 字节。
- PDF SHA-256：`6b4e8e6959081d7e8c9cec06ebe69d60b7913e47b00ac4ef79dcfe16b764b968`。
- 先固定 [预期事实清单](expected-facts.md) 与 [golden.json](golden.json)，后执行 [生成器](generate_pdf.py)。生成器不读取清单或 golden，不读取模型抽取输出。
- 清单、golden、来源说明的哈希和逐项程序检查见 [validation.json](validation.json)。当前 golden 为 agent 编写、待用户审核，不标记为 human confirmed。

## 制作与本轮修改的检查

1. 首次制作时确认并复用原 PDF：60 页、8,850,643 字节，哈希见[来源说明](source-adaptation.md)。从原文件读取必要第 14、18 页文字，查看对应缓存渲染；只核对已选事实。
2. 本轮按用户要求删除第 5 节“来源与改编”，将唯一输入 PDF 迁入 `samples/synthetic/bridge-demo-001/input/`；删除旧目录中的 PDF 并清理空目录。来源说明保留在独立材料中。重新生成两页 PDF，嵌入 Arial Unicode 字体；使用 bundled ReportLab，不安装依赖。
3. 使用 bundled Poppler 以 130 dpi 渲染两页，agent 逐页视觉查看。两页中文、m²、标题、表格、页眉页脚和页码均清晰，未见缺字、裁切、重叠或跨页断表。此检查不是用户人工验收。
4. 使用 pdfplumber / pypdf 比对固定清单，39 项产物检查通过：正文目标句、表格各行、桥梁与构件、三组病害和观测编号、三个数量与面积、否定句、编制日期及观测日期未知、两页标记、页码和字体嵌入，以及第 5 节、真实参考桥名和原报告页码说明均未出现在 PDF 中。实际表格共 5 个（含演示声明框）。
5. 确认 golden 与可读清单的写入时间早于 PDF；匹配脚本仅做产物核对，未生成业务实体、候选抽取结果或 Evidence。
6. 新增 Markdown 相对文件链接及交接指针已检查；PRD、spec 和历史讨论未编辑。Git 仍为 main，无提交/HEAD；现有未跟踪文档保留。

## 重现命令

在项目根目录执行，使用本机 bundled runtime；Arial Unicode 字体来自本机 `/Library/Fonts/Arial Unicode.ttf`，未复制字体文件。其他机器需提供可嵌入并覆盖中文及 m² 的字体，调整生成器字体路径。

```bash
/path/to/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 samples/synthetic/bridge-demo-001/generate_pdf.py
/path/to/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 samples/synthetic/bridge-demo-001/verify_pdf.py
mkdir -p tmp/pdfs/bridge-demo-001
pdftoppm -r 130 -png samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf tmp/pdfs/bridge-demo-001/page
```

ReportLab 会写入重新生成时的 PDF 元数据，因此重新生成后字节哈希可能变化；应重新核对并更新核验记录。本轮渲染临时图已在查看后清理，最终 PDF 和核验材料保留。

## 仍未验证

- 用户对事实、位置简化、来源映射和版式的审核。
- Docling、OCR、模型/规则抽取、业务 Schema 的面积字段定稿、数据库、检索或网页证据定位。
- 正式 Evidence 的 block_id / bbox / charspan。事实清单只给新 PDF 页码与目标文字，不伪造这些字段。
- 合成素材对真实桥梁检测报告的适用性、多次真实观测历史或专业工程结论。

下一步仅等待首份人工评审；用户确认或提出修改后，再决定扩展变体。本轮未批量生成、未安装解析器或数据库、未构建系统。
