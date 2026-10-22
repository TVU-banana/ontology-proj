# Kimi K3 UI/UX 优化结果

执行时间：2026-10-12（Asia/Shanghai）  
基线：main @ `2c7101eb94fae64ac3a3939a777ca025c0f85f1b`  
执行模型：`cn:kimi-k3-1`（本次会话配置身份，未独立核实上游真实 revision）

## 1. 现状主要问题（before 证据：screenshots/before/，基线文件来自 `git show HEAD`，静态服务 18123）

1. 工作台页首导航是无样式的纯文本链接（默认蓝色下划线），与整体工具风格不一致；主题切换控件小且与正文混排。
2. 两页 token 不统一：工作台只有一张 850 字节补丁样式表，色板/间距/圆角没有系统化；状态文字与说明文字没有视觉容器，与正文难区分。
3. 正文字号 15px、输入控件 14px、按钮 min-height 40px，均低于可读与触摸目标基线；`.identifier` 11px 过小。
4. 工作台“共享资料范围”与其他三区块同权重平铺，共享范围这一核心上下文不突出；录入区主动作（上传）与辅助操作（读取/跟踪/重试/索引）同为实心按钮，层级不分明。
5. 查询页无返回工作台的入口，只能改 URL；窄屏无跳转主内容链接，键盘用户需逐个 Tab 过导航。
6. 错误状态仅靠红色文字表达，没有背景/边框等冗余编码；focus 颜色（#dd9b08）与品牌色板脱节。

## 2. 设计方案与变化

统一两页共享 token（query.css）：中性灰蓝底、单一 teal 强调色、浅/深/system 三主题（显式选择优先于系统偏好）、16px 正文、系统中文字体栈（PingFang SC / Hiragino Sans GB / Microsoft YaHei / Noto Sans CJK SC）、10px 圆角、统一 4/8/12/16/24/32 间距刻度。无大面积装饰、无 hero、无表情图标、无虚构统计。

- 状态/说明文字使用 `.status` 容器（柔和底 + 细边框），错误态追加 `--error-bg` 背景与边框，不再只靠颜色。
- 按钮双层：主动作实心、`.actions` 内辅助操作（读取/跟踪/重试/停止/索引）描边；所有按钮、导航链接、引用按钮 min-height 44px。
- 工作台：`#scope-panel` 通栏 + 左侧强调条，三列网格排布桥梁/文档/构件；导航链接改为描边胶囊按钮；录入/问答表单与后续操作之间加虚线分隔。
- 查询页：新增“返回资料与证据工作台”普通链接；两页均新增跳转主内容链接（默认视觉隐藏、Tab 聚焦可见）。
- 语义化：main#id、各 section `aria-labelledby`、nav `aria-label`；`.identifier` 改 13px 等宽字体，长 ID 完整换行可读。
- PDF 证据区结构（`#page{position:relative}` + `#highlight{position:absolute}` + `#pdf-scroll`）原样保留；滚动/缩放/旋转/高亮实测正常。
- reduced-motion 下全局禁用过渡动画；focus 统一 3px `--focus` 描边（浅色 #b25e00 / 深色 #f0b45c）。

语义保持不变：synthetic/演示标识、demo_accept_all、专业审核未执行/not_run、未知日期、局部身份、pending 等文案与含义未改；仅把策略说明放进 `.status` 容器，文字未变。

## 3. 修改文件与原因

| 文件 | 原因 |
|---|---|
| `src/ontology_proj/interfaces/http/static/query.css` | 共享设计 token、两页基础控件、主题、可访问性、PDF 区样式 |
| `src/ontology_proj/interfaces/http/static/workspace.css` | 工作台网格布局、scope 面板、表格/证据链接/agent 事件样式 |
| `src/ontology_proj/interfaces/http/static/workspace.html` | skip-link、语义标签/aria、状态容器 class、scope 网格包装、导航 aria-label、CSS 版本号 v=2→v=3 |
| `src/ontology_proj/interfaces/http/static/query.html` | skip-link、返回工作台链接、语义标签/aria、状态容器 class |

DOM 契约：基线 50+34 个既有 id 全部保留、tag 与稳定属性（name/type/value/required/checked/disabled/hidden/href/src/accept/target/rel）零变化；script 标签与 option 完全一致；无 inline handler；新增 id 仅 `main` 与各 section 标题（scope-title 等），JS 不引用。证据：`checks/dom-contract-diff.txt`、`checks/js-selector-coverage.txt`（两个 JS 引用的全部静态/动态 id 均在 HTML 中存在，无缺失；form/button 归属关系与基线一致，未新增 form 内误触 submit 的按钮）。

## 4. 实际验证（全部真实执行，原始证据在 checks/）

| 检查 | 命令/方法 | 结果 |
|---|---|---|
| 范围审计 | `python3 docs/ui-ux/20261011-kimi-k3/audit_scope.py` | 退出码 0，passed=true，0 violations；仅白名单 4 文件 + .handoff/latest.md（派发方授权记录）变更，1974 基线文件其余哈希保持，全部 JS 未变。`checks/scope-audit.json` |
| 回归测试 | `.venv/bin/python -m pytest -q tests/unit/test_agent.py tests/integration/test_http.py` | 退出码 0，26 passed。`checks/pytest.txt` |
| 空白检查 | `git diff --check` | 退出码 0 |
| 对比度 | 20 组前景/背景 token 对 WCAG 计算 | 全部 ≥ 4.5:1（最低 6.19:1）。`checks/css-contrast.txt` |
| 页面级水平溢出 | headless Chrome eval scrollWidth-clientWidth | 两页 × 390px/375px 全为 0。`checks/overflow-checks.txt` |
| 行为保持 | 真实服务 18000 上的加载/主题/锚点/details/参数联动/控制台 | 全部通过，console 无新增错误。`checks/behavior-checks.txt` |
| 真实数据渲染 | GET 读取真实已登记资料；POST /api/graph/lookup（既有只读查询）；GET /api/evidence 打开引用 | 状态/计数/长 ID/SYNTHETIC 标识完整；quote 高亮、PDF 渲染、黄色框、125% 缩放、90° 旋转正常 |
| 键盘/可访问性 | Tab 序列、skip-link、focus-visible、reduced-motion、200% 根字号 | skip-link 聚焦可见（3px outline）；reduced-motion 下 transition=none；200% 下无溢出、主按钮不被遮挡 |

**mock 与真实证据的区分**：曾尝试 agent-browser network route 拦截 mock 查询/证据响应，本环境拦截不生效（请求均到达服务器），已全部清除，未在任何截图或证据中使用 mock 内容。最终证据全部来自既有真实服务（18000，未重启，StaticFiles 直接读工作区文件）的真实已登记 synthetic 演示数据。期间共发出 7 次 POST /api/graph/lookup（既有只读查询语义，无业务写入；1 次 422 空范围校验）与若干 GET；无任何生成/上传/处理/重试/索引写请求。

**浏览器缓存插曲**：首个 Chrome profile 磁盘缓存滞留旧 query.css（首版 3407 字节），导致真实服务页面一度按旧样式渲染；换全新 profile 后确认服务器响应与工作区文件 md5 一致、68 条新规则全部生效。最终全部截图与验证均在干净 profile 完成。

## 5. 截图索引（全部已逐张查看）

before/（基线 HEAD，静态服务 18123，真实渲染基线源码）与 after/（真实服务 18000）各 8 张标准组合：

| 页面 | 主题 | 视口 | before | after |
|---|---|---|---|---|
| / | light | 1440×1000 | before/workspace-light-1440x1000.png | after/workspace-light-1440x1000.png |
| / | dark | 1440×1000 | before/workspace-dark-1440x1000.png | after/workspace-dark-1440x1000.png |
| / | light | 390×844 | before/workspace-light-390x844.png | after/workspace-light-390x844.png |
| / | dark | 390×844 | before/workspace-dark-390x844.png | after/workspace-dark-390x844.png |
| /query | light | 1440×1000 | before/query-light-1440x1000.png | after/query-light-1440x1000.png |
| /query | dark | 1440×1000 | before/query-dark-1440x1000.png | after/query-dark-1440x1000.png |
| /query | light | 390×844 | before/query-light-390x844.png | after/query-light-390x844.png |
| /query | dark | 390×844 | before/query-dark-390x844.png | after/query-dark-390x844.png |

功能状态补充截图（after/，真实服务真实数据）：

- `workspace-realdoc-light-1440x1000.png`：读取真实资料状态（长 document_id、operation_id、候选/接纳/待复核计数、警告）。
- `workspace-systemdark-1440x1000.png`：system 主题 + 模拟系统深色生效。
- `query-results-light-1440x1000.png` / `query-results-dark-1440x1000.png`：真实查询结果（2 构件、72 字符 cmp_ ID 完整、SYNTHETIC/接纳/复核状态）。
- `query-evidence-light-1440x1000.png` / `query-evidence-dark-1440x1000.png`：真实证据面板（quote 高亮、PDF 渲染、黄色框标示引用区域）。
- `query-evidence-zoom-rotate-light.png`：125% 缩放 + 90° 旋转后 canvas 正常。

查看结论：16 张标准截图 + 7 张功能截图逐张查看，未发现重叠、裁切、低对比或内容缺失；深色模式文字/边框/输入控件可读；窄屏两页均按逻辑顺序单列堆叠。

## 6. 功能保持证据

- DOM/控件契约零变化：`checks/dom-contract-diff.txt`（missing/changed 均为空）。
- JS 选择器兼容：`checks/js-selector-coverage.txt`（workspace.js 22 个静态 id + 12 个动态 id、query.js 29 个 id 全部存在于 HTML；form/button 归属与基线一致）。
- 行为实测：`checks/behavior-checks.txt`（主题切换、query-link 动态参数、锚点、details、查询类型切换 identity 字段、真实资料读取、真实查询、证据打开、缩放/旋转）。
- 回归：26 项既有测试通过；范围审计确认全部 JS/Python/测试/配置哈希不变。

## 7. 未验证/不足

- 报告生成、问答生成、Agent 运行、上传/处理/索引等写路径未触发（计划禁止）；其输出区样式（报告表格 `.table-scroll`、`.evidence-links`、`.agent-event`）沿用既有类名并做了视觉 token 化，但未用真实生成结果渲染验证——这些类名与结构未变，风险低。
- 真实 `/evidence/{id}` 直达路径（location.pathname 分支）未单独截图；该路径复用同一 openEvidence 函数与证据面板，查询页点击引用已实测通过。
- 触摸设备实机未测（仅视口模拟与 44px 目标尺寸静态保证）。
- 中间曾误发 6 次额外 POST /api/graph/lookup（拦截调试期间），均为既有只读查询，不影响业务数据；已在交接中如实记录。

## 8. Deferred（白名单内无法实现的交互改善）

- 长 ID 一键复制按钮：需要新增业务控件/脚本，冻结范围不允许。
- 查询结果分页/虚拟滚动与报告区块折叠：需要改 JS 行为，冻结。
- 主题选择持久化（localStorage）：需要改 JS，冻结。

## 9. ADR 检查

无决策变化。纯视觉与语义标签优化，无产品范围、业务语义、架构边界、存储或技术选型变化；PRD/spec/ADR 正文未改。
