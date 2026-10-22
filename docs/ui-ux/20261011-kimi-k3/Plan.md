# Kimi K3：前端 UI/UX 优化执行计划

## 1. 授权与目标

用户已授权你直接自主实施本项目的 UI/UX 优化，仅限界面呈现，不修改任何其他功能代码。你是执行 Agent，Codex 根据最终证据独立验收。不要等待新的计划批准，不持续询问 Codex，也不自行委派其他模型。全程使用当前指定的 Kimi K3。普通界面编辑已获授权；全局 AGENTS 中的删除/发布确认规则不应被解释为每次编辑都需要批准。

工作目录 `/path/to/user/project/ontology-proj`。基线分支 main，HEAD `2c7101eb94fae64ac3a3939a777ca025c0f85f1b`，派发前工作区干净。先读根 AGENTS.md、.handoff/latest.md 的当前摘要及本计划；按需读 Spec007/Spec003 的页面契约。不要通读历史实验或私有数据。当前任务不是 S5.2 验收或功能扩展。

成功结果：资料与证据工作台 `/` 和精确查询/原文页 `/query` 有统一、克制、专业的中文工具界面；层级清晰、常用操作易找、范围与结果易区分；浅深色均可读，窄屏可操作；全部原有业务行为保持。

## 2. 严格修改范围

应用代码唯一白名单：

- `src/ontology_proj/interfaces/http/static/workspace.html`
- `src/ontology_proj/interfaces/http/static/workspace.css`
- `src/ontology_proj/interfaces/http/static/query.html`
- `src/ontology_proj/interfaces/http/static/query.css`

可以新增容器、CSS class、语义标签、aria-label、跳转主内容链接、静态内联 SVG；可以调整视觉文案但不能改变含义。禁止添加脚本、inline event handler、网络资源、依赖、构建工具、框架、字体下载或新增业务控件。

**所有 JavaScript（workspace.js、query.js、PDF.js 等）、Python、测试源码、API、Schema、配置、模型提示词、服务进程、业务数据一律不修改。** 不改变既有 DOM id、表单关系、控件 name/type/value/required/checked/disabled/hidden、select option 的值和顺序、链接参数或脚本来源。保留脚本依赖的 DOM 结构语义和事件默认行为；button 不移出所属 form，不新增误触 submit 的 button。静态资源 CSS 查询版本可仅为缓存更新调整；JS URL 不变。禁止用 CSS 隐藏操作/警告/失败/长 ID，禁止覆盖 `[hidden]` 行为，禁止破坏 PDF canvas/高亮坐标与定位结构。

可写交付目录 `docs/ui-ux/20261011-kimi-k3/` 的 results.md、result.json、checks/、screenshots/、新增必要验证脚本（不得覆盖 Plan.md、baseline.json、audit_scope.py、launch.json）。`.handoff/latest.md` 只允许追加/插入本次结果摘要和链接，不删除历史；ADR 检查无业务/架构决策变化时注明“ADR 检查：无决策变化”。PRD/spec/ADR 正文无需因纯视觉优化而改写。不要 commit/push、安装软件、清数据、重启现有服务、修改 DSH 配置、读取凭据或 .env、调用本项目生成/上传/处理/重试/索引 API。

遇到范围外问题仅记录，不修复。不可能在白名单内实现的交互改善标记 deferred。

## 3. 设计方向与实施顺序

1. 检查两个 HTML/CSS 和只读阅读 JS 的 selector/呈现契约。确认每个功能入口、状态容器、默认值与安全说明。
2. 在 results.md 简述主要现状问题和拟采用设计，再直接实施：统一语义色/空间/字体/圆角/边框 token，采用系统中文字体；中性色底、清晰强调色；16px 左右正文字号、可读行距；避免大面积装饰、营销 hero、表情图标或虚构统计。
3. 工作台突出共享范围与录入→问答→报告流程。主动作与读取/跟踪/重试等辅助操作分层；Agent 用现有 details 保持渐进展开。长 ID、原文、面积 m² 和报告内容必须完整可看。
4. 查询页保持范围→结果→原文证据逻辑；桌面形成稳定双栏，窄屏按逻辑顺序堆叠；保留 PDF 滚动/缩放/旋转/高亮能力及证据链接。允许添加回工作台的普通链接。
5. 同时定义显式 light/dark 以及 system 主题，不使系统深色覆盖用户显式浅色。普通文字对比度至少 4.5:1，focus 清晰，状态不能仅靠颜色；触摸目标至少44px；hover/focus/disabled 可识别；尊重 reduced-motion；避免装饰性持续动画。
6. 保留 synthetic/演示标识、demo_accept_all/专业审核未执行、unknown 日期、局部身份、pending/失败、not_run、五章报告等语义。不可美化成“已人工确认”“正式质量评估”等错误结论。

参考：Codex 已运行 ui-ux-pro-max 设计检索，适用建议为 accessible、克制中性色、清晰焦点、统一 token、移动布局；其 FAQ 营销布局不适用于此项目，不套用。可只读参考 `/path/to/user/.codex/skills/ui-ux-pro-max/SKILL.md`，不需安装工具。

## 4. 必须执行的验证

先使用已有工具，不新增依赖。相关检查命令以本仓库现状为准：

- `.venv/bin/python -m pytest -q tests/unit/test_agent.py tests/integration/test_http.py`：使用默认隔离/替身设置，不启用真实图配置，不跑旧实验。完整记录命令、退出码、结果，失败如实保存，不修改测试迎合代码。
- `git diff --check`；对比基线 DOM id/控件关键属性/脚本与行为入口；逐项核查 form/button 和 JS selector 兼容性。
- 执行 `python3 docs/ui-ux/20261011-kimi-k3/audit_scope.py`；必须验证白名单之外全部基线文件哈希保持、所有 JS 保持。不删除/修改 baseline 或核验器。检查结果保存到 checks/。
- 浏览器实际渲染浅色/深色、桌面1440×1000、手机390×844（至少两页×两主题×两宽度，8张最终截图），补375px溢出检查。最好同条件 before/after 对照；基线源码可从 git show HEAD 只读获取、临时预览，不 checkout/reset 工作区。
- 验证页面级 horizontal overflow 为0（PDF/表格容器内部滚动可接受），键盘 Tab 可达主要控件、focus 可见、200%字号/缩放不遮挡关键控件、reduced-motion。视觉截图必须实际查看，记录是否重叠/裁切/低对比。
- `/` 与 `/query` 加载、主题切换、现有 details、锚点导航、查询类型切换和对应字段显示保持；浏览器 console 无新增错误。可对 API 使用隔离 mock/fixture 验证已有输出状态/长内容/原文/报告，不向真实业务 API 发送写请求；mock 与真实服务证据分开。读已登记资料只能 GET，禁止把演示操作转成真实模型/数据写入。

已知工作台可能在 `http://127.0.0.1:18000/` 运行；不要再启动同 DATA_ROOT 应用，不停/重启现有进程。如浏览器受限，可用既有浏览器自动化或临时静态服务/隔离 mock 页面。不要突破沙盒或申请 danger-full-access。无工具/权限时明确 `blocked`/`partial`，不能用静态检查冒充渲染验收，也不能伪造截图；仍交付已完成的安全修改与剩余项。

## 5. 特定交付格式与完成判定

最终写 `results.md`：主要问题→变化→文件与原因→实际验证→截图索引→功能保持证据→未验/不足→ADR 检查。不要大段复制日志；原始日志放 checks/。必须说明哪些只是 mock，哪些是实际页面渲染。

最终写 `result.json`（合法 JSON）：

```json
{
  "status": "complete|partial|blocked",
  "model": "cn:kimi-k3-1",
  "base_head": "2c7101eb94fae64ac3a3939a777ca025c0f85f1b",
  "summary": "中文结果",
  "changed_files": [{"path": "相对路径", "reason": "用途"}],
  "scope_audit": {"passed": false, "evidence": "checks/scope-audit.json"},
  "behavior_preservation": {"passed": false, "evidence": "实际审查文件"},
  "checks": [{"name": "检查", "command": "实际命令", "exit_code": 0, "passed": true, "evidence": "日志路径"}],
  "screenshots": [{"page": "/", "theme": "light", "viewport": "1440x1000", "path": "screenshots/实际文件.png", "inspected": true}],
  "visual_findings": [],
  "unverified": [],
  "deferred": [],
  "adr": "无决策变化"
}
```

只有范围审计、行为保持、相关回归通过，且8种组合均有实际截图并查看无明显问题，才可标记 complete。每个 passed 都需真实证据；仅进程退出0不等于完成。结果的 model 是此次配置身份，不宣称已独立核实上游真实模型 revision。

最后输出一段中文结论并单独输出 `UIUX_RESULT=docs/ui-ux/20261011-kimi-k3/result.json`。无论成功或受阻都生成两份结果文件。自主推进至完成或明确受阻，不持续向 Codex 汇报进度。
