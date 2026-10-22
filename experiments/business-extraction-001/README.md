# 单样本业务抽取实验

状态：本地中转鉴权和真实抽取已运行；当前两页 synthetic PDF 的核心基线通过。先读 [验收报告](results/review.md)，真实候选与记录在 `results/fresh/`；这是隔离实验，正式应用与数据库尚未实现。

实验复用 [Docling 实际解析结果](../docling-parse-001/results/normalized.json)，按照 [最小业务契约](../../specs/001-business-extraction/contract.md) 输出候选，再由程序校验并形成记录。验收参考 [独立预期事实清单](../../samples/synthetic/bridge-demo-001/expected-facts.md)，用户已确认本轮核心验收基线，见 [确认记录](results/baseline-approval.json)。清单与 golden 不作为模型输入。

## 本地配置

[.env.example](.env.example) 是无密钥模板；同目录 `.env` 为本地私有配置，已加入忽略规则。首次准备时已复制模板，权限设为 `600`，请在本地填入 `EXTRACTION_API_KEY` 后保存。

| 变量 | 用途 |
|---|---|
| EXTRACTION_BASE_URL | 用户提供的中转 API 地址，含 `/v1` |
| EXTRACTION_MODEL | 用户提供的候选模型标识，当前为 `cn:deepseek-v4.1-flash` |
| EXTRACTION_API_KEY | 中转访问密钥，仅保存在本地 |

变量名是本实验的配置约定。[provider.py](provider.py) 显式加载配置，通过 curl 访问中转，密钥通过标准输入传递；日志与结果排除密钥及 Authorization 请求头。项目自带的现有 Docling 实验虚拟环境包含 jsonschema，未安装新依赖。

## 接口探测与下一步

2026-10-10 对用户给定地址请求 `GET /v1/models`，禁用代理、连接超时 5 秒、总超时 10 秒，收到 HTTP `401`。这证明当前执行环境能访问该地址，接口要求鉴权；尚未证明 OpenAI Chat Completions 兼容性、模型可用性或上游推理能力。摘要见 [connection-check.json](connection-check.json)。

后续鉴权与最小 JSON 探测均返回 200，所配模型可用，关闭推理参数实际生效；详情见 [provider-check.json](results/provider-check.json)。长输出曾带代码围栏，适配器仅移除覆盖整个响应的单一完整围栏；原响应和格式诊断保留。

## 复现

从仓库根目录运行。模型调用使用真实服务、会产生用量；本机 Codex 沙盒中 `.local` 网络请求需主机网络权限，离线校验无需网络。

```bash
# 可选接口探测（会调用模型）
experiments/docling-parse-001/.venv/bin/python experiments/business-extraction-001/provider.py
# 新调用：只读解析与 Schema，run-id 不得复用已有目录
experiments/docling-parse-001/.venv/bin/python experiments/business-extraction-001/extract.py --run-id another-run
# 以下只检查已有产物，无模型调用
experiments/docling-parse-001/.venv/bin/python experiments/business-extraction-001/validate.py experiments/business-extraction-001/results/fresh
experiments/docling-parse-001/.venv/bin/python experiments/business-extraction-001/evaluate.py experiments/business-extraction-001/results/fresh
experiments/docling-parse-001/.venv/bin/python experiments/business-extraction-001/test_validator.py
```

检查新调用时，将验证/对照命令中的 `fresh` 换成 `another-run`。候选被拒绝可使用 `--repair-from another-run --run-id another-repair` 做一次显式修正；输入只增加前轮输出与程序诊断，不加入 golden。不存在自动无限重试。

代码职责：[extract.py](extract.py) 适配文档并调用模型；[validate.py](validate.py) 校验与构建记录；[evaluate.py](evaluate.py) 后置独立对照；[test_validator.py](test_validator.py) 离线反例测试。源码版本及输入摘要见 [integrity-check.json](results/integrity-check.json)。
