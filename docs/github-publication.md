# GitHub 源码发布

## 发布范围

GitHub 仓库发布应用代码、测试、依赖锁、设计契约及显式审阅的合成样本。
发布清单由 [`.publication.json`](../.publication.json) 和 [导出程序](../scripts/export_publication.py) 管理。
默认排除私有配置、运行数据、真实 PDF、原始模型请求响应、日志、截图、审计快照和历史验收目录。
离线测试必需的合成 JSON 是逐文件例外，保留原路径，便于现有回归测试运行。

本地旧提交和原始证据保留。首次发布使用独立目录、新建 Git 历史，Git 作者使用 GitHub noreply 邮箱。
本地归档中的个人路径、局域网地址和邮箱只在导出副本中替换；上游 PDF.js 和许可证原字节保留。
公开快照的 `.handoff/latest.md` 由导出程序重新生成，不包含本机历史交接。
公开版本中，历史文档的部分 `results`、`docs/audits`、`docs/demo`、截图链接指向未发布的本地归档。
这些原始记录不能用公开快照重新校验；公开快照不冒充原始证据的逐字节副本。

## 从本地归档更新发布副本

先审阅源项目修改并本地提交，再导出到一个不存在的新目录。导出只接受 Git 已跟踪文件，不会复制 `.git`。

```bash
python3 scripts/export_publication.py --output data/github-release-next
cd data/github-release-next
git init -b main
git add .
python3 scripts/export_publication.py --check
python3 -m unittest discover -s tests/security
gitleaks dir . --redact=100 --no-banner
```

发布副本运行完整离线回归时，需使用本项目 Python 3.12 环境，确保 `PYTHONPATH` 指向副本的 `src`。
提交后再运行 `gitleaks git . --log-opts=--all --redact=100 --no-banner`，扫描真正待推送的历史。
检查通过后才允许推送。更新已有 GitHub 仓库时，基于其公开历史同步新快照，正常提交；不要强推旧本地历史。
首次发布目录和验证结果记录在本地 `.handoff/latest.md`；私有扫描清单位于忽略的 `data/` 中。

## 新资料与密钥

- 本地配置使用 `.env`、`*.env` 或 `deploy/.env.neo4j`，不要修改 `.env.example` 存放真实值。
- 上传资料、解析结果、报告、模型缓存统一放在 `data/`、`input-pdfs/`、`output/` 等忽略目录。
- 新增合成测试资料时，先确认不含真实用户信息；确有必要时逐文件加入发布清单。
- `.gitignore` 与发布清单互相补充；两者都不是密钥扫描的替代品。

## 验证边界

本次发布是 GitHub 源码托管。现有本机测试不证明公网服务安全；公网运行边界见 [SECURITY.md](../SECURITY.md)。
扫描可以证明已检查的文件和规则下未发现问题，不能承诺绝对不存在未知秘密。
