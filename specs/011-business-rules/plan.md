# 规则配置与面积表达兼容

2026-10-13用户明确采纳方案并授权实现。main/7847bd3，保留所有旧实验、首次失败、golden与其他会话未提交修改。OCR/图片专业诊断/桥梁专用登记泛化不在本轮。

## 契约

统一资源business-rules.json：带版本的面积/长度单位别名与Decimal比例、类型术语别名、面积标记和语义限定。配置由适配器读取/校验，核心计算和证据检验用纯数据规则对象，不能把任意脚本当配置执行。不引入spaCy/Pint或通用规则DSL。

旧0.1/0.2候选与记录兼容；新抽取使用0.3.0-draft，identity_mode=explicit|registered，registered仍需原受控来源，不放宽跨文档登记。新版Observation候选增加area_expression（原文连续表达或null），面积计算由程序执行。新版正式记录增加area_basis、area_expression、area_derivation（方法/输入值单位/结果/规则版本）；area_original_value/unit仅原文直接面积，尺寸/面积乘积不塞入原始数值。area_value仍归一m²，conversion=identity|unit_scale|rectangle_product|explicit_area_product|null。

直接面积支持m²/㎡/m2/平方米，cm²/cm2/平方厘米，mm²/mm2/平方毫米及空白形式。面积标记/表头和数值单位须配对，不用同句任意数字当面积。单处肯定可表达为一处/存在/发现等，但否定、疑似、汇总始终不能晋升肯定单处；不凭语义相似放宽原文定位/身份。

尺寸只允许同一连续原文中明确矩形+长/宽+两个明确长度单位，或明确面积标记S/面积的两个数乘积+面积单位（或两个明确长度单位相乘的面积式）。尺寸不规则/最大范围/上限/缺单位/多组或原文面积与计算冲突拒绝或pending；保留近似。计算仅两个非负十进制数，×/乘以/*等白名单，不执行eval；Decimal有有界精度，输入长度限制。

同步正式记录再校验、固定图标量投影（area_derivation以JSON字符串进图，读时解码）、查询、问答成对证据和固定报告：显示直接/派生及近似，能复算才能呈现。原字面/引用不改，不同对象/检测不因相同面积合并。

## 执行

T01冻结契约/独立预期→T02配置/纯测量解析→T03候选与正式校验/Schema/提示词→T04图/问答/报告接通→T05新边界反例与旧回归→T06本地隔离保存/投影/展示验证、文档/ADR/交接。

不调用真实模型、不重跑旧成功Docling或模型实验；本轮真实服务状态只核对不写旧root/图。前端只补面积来源/原始表达的功能提示，不做视觉美化。源表/模型输入替身与真实规则计算、文件保存明确区分。升级Schema与语义版本记录新ADR，不降低旧验收。

社区依据：[spaCy组合规则与模型](https://spacy.io/usage/rule-based-matching)、[Pint量纲/单位](https://pint.readthedocs.io/en/stable/getting/tutorial.html)、[CheckList行为测试](https://github.com/marcotcr/checklist)。这里借鉴方法，不宣称其实现已用于本项目。字段主定义见[业务契约§4.2](../001-business-extraction/contract.md#42-observation)；决策见[ADR-017](../../docs/adr/ADR-017-规则配置与可追溯面积派生.md)。
