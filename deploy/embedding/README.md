# 本地共享嵌入服务

部署日期：2026-10-11；用户确认 Qwen3 Embedding 0.6B。模型选择理由见 [ADR-007](../../docs/adr/ADR-007-本地共享Qwen嵌入服务.md)。这是一项 S3.2 前置部署，项目语义检索尚未接入。

## 实际配置

| 项目 | 配置 |
|---|---|
| 运行时 | 原生 macOS Ollama 0.40.3，Apple M4 / Metal |
| 模型 | `qwen3-embedding:0.6b`，595.78M 参数，GGUF / Q8_0；不是原始精度权重 |
| 模型内容标识 | `sha256:ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d` |
| 输出 | 当前实测 1024 维有限、非零、单位长度向量 |
| 原生接口 | `http://127.0.0.1:11434/api/embed` |
| OpenAI 兼容 Base URL | `http://127.0.0.1:11434/v1`，调用 `/embeddings` |
| 鉴权 | 本地服务不验证 API key；要求 key 的客户端可填 `ollama` |
| 运行上下文 | 本次配置 4096 tokens；模型声明上限 32768，不表示当前每次使用此上限 |
| 生命周期 | 用户 LaunchAgent 登录时启动，服务退出后自动重启；模型空闲 5 分钟卸载，服务保持运行 |
| 网络 | 仅监听本机 IPv4 回环，`OLLAMA_NO_CLOUD=1`；首次下载访问官方仓库 |

模型与服务安装在用户目录，可由多个本机项目共用，无需为每个项目复制权重。各项目仍须独立管理切块、来源、权限和向量索引。

## 调用

文档批量嵌入，不为文档添加查询指令。原生接口用 `truncate:false`，超长明确报错；按 token 切块，不能只用汉字数猜长度。

```bash
curl --fail-with-body http://127.0.0.1:11434/api/embed \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":["桥面铺装发现坑槽。","API 支持批量输入文本。"],"truncate":false,"options":{"num_ctx":4096}}'
```

检索问题按官方格式添加任务指令，待检索文档保持原文。其他项目可明确自己的任务指令，并固定其版本。

```bash
curl --fail-with-body http://127.0.0.1:11434/api/embed \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":"Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery: 桥面有哪些病害？","truncate":false,"options":{"num_ctx":4096}}'
```

OpenAI 兼容示例：

```bash
curl --fail-with-body http://127.0.0.1:11434/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":["第一段文本","第二段文本"],"encoding_format":"float"}'
```

兼容接口的批量、顺序、维度与原生一致性已测；其超长输入拒绝行为本轮未测。需要严格防止截断时采用上面的原生接口。还未测试可选降维、并发大批量或 Docker 内客户端访问。

## 本机管理

- CLI：`/path/to/user/Applications/Ollama.app/Contents/Resources/ollama`
- 服务配置：`/path/to/user/Library/LaunchAgents/com.example.local-embedding.plist`
- 权重：`/path/to/user/Library/Application Support/LocalEmbedding/models`
- 日志：`/path/to/user/Library/Application Support/LocalEmbedding/logs/`

```bash
# 查看状态
launchctl print gui/$(id -u)/com.example.local-embedding
curl --fail http://127.0.0.1:11434/api/version

# 重启服务
launchctl kickstart -k gui/$(id -u)/com.example.local-embedding

# 停止当前服务；配置仍保留，下次登录仍可自动启动
launchctl bootout gui/$(id -u)/com.example.local-embedding

# 停止后重新启动
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.example.local-embedding.plist"
```

不使用 GUI App 启动第二个默认端口服务。不要在已有索引上隐式更新模型；API 模型名可保持相同，但内容摘要改变仍是模型变化，需要重建向量。Q8_0、指令、维度和归一化方式均属于嵌入配置的一部分。

## 验证与边界

先固定 [合成服务用例](smoke-cases.json)，再执行 [实际检查脚本](../../scripts/verify_local_embedding.py)。首轮 [13 项检查](../../specs/004-semantic-context/results/preparation/local-embedding-smoke-01.json)全部通过，包含原生/OpenAI 批量、重复一致性、有限单位向量、四个中英文检索小样例及非法/超长输入拒绝。模型载入时 API 报告约 2.37 GB GPU 分配，此值不是系统进程峰值内存。

首个四段调用约 4.247 秒（含模型加载），后续单段约 0.034 秒、四个问题约 0.124 秒；仅这次短文本测量，不外推为持续吞吐或性能承诺。[重启记录](../../specs/004-semantic-context/results/preparation/local-embedding-restart.json)保存实际服务重启与再次嵌入结果；登录自动启动由配置启用，未实际注销/重启电脑验收。

本轮没有模型 A/B、专业人工审核、全量跨项目语料测试或 Neo4j 向量检索验收，不算 S3.2 完成；业务应用的配置与依赖未修改。复验应使用新的结果文件，不覆盖首轮。

[安装核验](../../specs/004-semantic-context/results/preparation/local-embedding-install.json)记录官方包 SHA-256、应用签名及三份实际 blob 的哈希/尺寸通过。首次把本地外层 manifest 的原始字节哈希与 API 摘要直接比较导致失败，见 [初检](../../specs/004-semantic-context/results/preparation/local-embedding-install-check-01.json)；后续确认 API 摘要对应外层引用的原始 manifest blob，分别验证两层摘要，未改写权重。

官方依据：[Ollama macOS](https://docs.ollama.com/macos)、[模型量化版本](https://ollama.com/library/qwen3-embedding:0.6b)、[原生接口](https://docs.ollama.com/api/embed)、[Qwen 查询指令](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)。
