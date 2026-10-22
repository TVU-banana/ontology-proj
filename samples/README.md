# 模拟输入样本

按来源性质与样本编号组织。每个样本的 `input/` 只放待录入资料；事实预期、制作溯源与核验材料放在样本目录外围，避免一并作为业务输入。

```text
samples/
  synthetic/
    bridge-demo-001/
      input/
        bridge-demo-001.pdf
      expected-facts.md
      golden.json
      source-adaptation.md
      generate_pdf.py
      verify_pdf.py
      validation.md
      validation.json
```

- 首份输入：[溪桥演示桥 PDF](synthetic/bridge-demo-001/input/bridge-demo-001.pdf)。
- 独立预期：[事实清单](synthetic/bridge-demo-001/expected-facts.md)；核心基线已获用户确认，依据见 [确认记录](../experiments/business-extraction-001/results/baseline-approval.json)，原文件历史状态保留。
- 制作溯源：[来源改编说明](synthetic/bridge-demo-001/source-adaptation.md)。
- 制作核验：[validation.md](synthetic/bridge-demo-001/validation.md)。

输入仍明确标记 synthetic；目录的输入角色不改变合成来源性质。此目录尚未接入应用，不代表已经实现录入。

受控变体当前入口：[bridge-variants-001/README.md](synthetic/bridge-variants-001/README.md)。版本 1.0 预期保持冻结，四份变体 PDF 已制作，101 项制作检查与 8 页视觉核验通过；变体 Docling 与 120 项解析核验已通过，模型实验尚未运行；详见 [解析验收](../experiments/docling-variants-001/results/review.md)。PDF 放入 input/，预期、冻结记录与核验材料保留在外围。
