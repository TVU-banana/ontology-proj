# 受控变体业务抽取：固定配置基线

版本：**1.0**；固定日期：2026-10-11。配置冻结时为离线准备、调用 0 次；随后用户授权的十次真实调用已完成，**严格验收 7/10，仅两份素材两次均通过，整组稳定性未通过**，见 [实际验收记录](results/review.md)。配置保持原样，没有修正轮。

主配置见 [baseline.json](baseline.json)，完整性记录见 [freeze.json](freeze.json)，72 项准备检查见 [preparation-checks.json](preparation-checks.json)。预期清单仍独立维护在样本目录，不发送给模型。

## 已固定内容

| 项目 | 固定值或依据 |
|---|---|
| 中转 | `http://relay.example.invalid:7863/v1`，Chat Completions |
| 模型标识 | `cn:deepseek-v4.1-flash` |
| response_format | `json_object` |
| temperature | `0` |
| max_tokens | `18000` |
| thinking | `disabled` |
| stream | `false` |
| Schema | `0.1.0-draft`，保存 [原文件快照](frozen/business.schema.json)及 SHA-256 |
| 系统提示词 | 与成功 fresh 请求逐字一致，保存 [提示词快照](frozen/system-prompt.txt)及 SHA-256 |
| 输入组织 | 原 extract.py 的适配和 messages 实现原样快照；固定 payload 字段、块顺序及 ID 映射规则 |
| 解析输入 | 原件及四份变体均保存当前已验收 normalized.json 快照，原文不清洗，页码来自该份解析 |
| 独立调用 | 每份两次，共十次；每次仅 system/user 两条消息，不带历史答案、诊断或预期 |
| 传输策略 | 连接超时 5 s、请求超时 180 s；不自动重试、修正或切换模型 |

冻结的 [extract.py](frozen/extract.py) 与 [provider.py](frozen/provider.py)只是原实现快照，用于复现和审计。原实验文件没有改动；快照脚本不要直接运行，其原目录常量应由套件入口显式绑定。

第三步只允许素材自身的块内容、document_id、表格、解析告警与来源位置随 PDF 改变，不按变体修改提示词或参数。块 ID 用 PDF 哈希和文档顺序重新生成，映射明确保存；证据仍以该份原始 Block.text/位置为准。

客户端能固定模型标识和请求参数，中转背后的具体模型权重版本不可由当前接口锁定，若返回模型或服务行为变化，应记录实际返回信息，不能宣称完全确定性。temperature=0 仍需重复调用测试。

## 离线准备与完整性

[prepare_requests.py](prepare_requests.py)仅导入冻结的纯适配/输入构造函数，未初始化 Relay、读取私有 `.env` 或访问网络。`prepared/` 中每个 case 保存 input-document.json、block-id-map.json，以及两份内容相同的待发送 request.json。原件的完整请求对象与已有成功 fresh 请求相等；所有素材保持相同 Schema 与系统提示词。

72 项检查核对源 PDF/解析摘要、原文/顺序/来源引用、参数、Schema、提示词及独立消息结构。两份相同请求代表重复实验的相同输入，不代表已经收到两份结果。预备请求没有 response/candidate/records 或验收结果。

第三步执行前先运行离线预检：

```bash
experiments/docling-parse-001/.venv/bin/python experiments/business-extraction-variants-001/verify_baseline.py
```

预检会拒绝冻结文件、原输入或预期变动。不得通过直接更新哈希来接受差异；需记录原因并创建新实验版本。准备脚本不覆盖现有 prepared/，正常复核使用 verify_baseline.py。

## 后续执行约定

用户授权第三步后，执行入口必须读取已冻结的 request.json 原样发送，不重新生成或临时修订 prompt；读取私有配置时核对中转地址与模型标识，密钥只从原 `.env` 获取，不写入冻结材料/请求/日志。使用 `Relay(config=原私有配置路径)` 并通过 `request('/chat/completions', 已冻结请求, timeout=180)` 发送，禁止调用 probe 增加额外模型请求。

每个 planned_run_id 创建新结果目录，保存原请求、原响应、用量、返回模型、finish_reason、耗时与失败诊断。超时、截断或解码失败保留当次结果，不自动补跑或修正。是否接受完整单一 JSON 围栏按冻结 extract.py 的 decode_output 处理，原响应仍保存。后续若改变规则或补跑，另列修正实验。

当前仅准备好了输入与配置，尚未实现套件网络执行入口或第四步对照程序；原 validate.py/evaluate.py 有单样本假设，不能直接当作变体套件验收。后续参数化只接入新样本，不静默修改验收规则；有必要改变行为时记录新版本并重跑整组。

## 实际调用与验收入口

用户随后已授权实际调用。新增 [run_requests.py](run_requests.py) 直接发送冻结请求，不运行 probe、不自动重试；结果写入 results/ 独立目录，原响应和首次失败保留。[evaluate_suite.py](evaluate_suite.py) 仅后置读取预期，复用原 validate.py 的未改规则，对五份素材的字段、关联、否定、缺项与分流逐项对照。新增执行/对照代码摘要见 [execution-code.json](execution-code.json)，6 项离线正反例检查见 [evaluator-checks.json](evaluator-checks.json)。这些入口在配置冻结之后增加，未改动 33 个已冻结文件或请求内容。

原 validate.py 的 SHA-256 必须等于前轮 integrity-check.json 记录；每份新的原文/位置快照直接送给校验函数，未为失败变体放宽规则。后置对照将说明错误分类为 negative/aggregate 视为失败，不用事后修改预期来接受它。results/calls.json 保存真实调用数量；配置和准备报告中的 0 次仅描述当时离线阶段。
