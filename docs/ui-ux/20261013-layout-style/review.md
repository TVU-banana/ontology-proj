# 布局与样式优化核验（2026-10-13）

用户授权使用 UI/UX Pro Max，仅优化布局与样式，不改变既有功能。接手 main / 7847bd3；原未提交修复、输入PDF、试用交接均保留。本轮未提交、未push。

## 交付

产品只改 workspace.html、query.html、workspace.css、query.css。工作台共享范围置左、任务顺序置右；录入内上传和既有操作并排；移动端自然单列。取消导航/模块/状态/辅助按钮的描边，空状态不留框；暗色采用近黑/冷灰/蓝色，浅色同步。主题选择置顶右，现有原生折叠区增加展开标记；保持所有入口可达，不增加任务切换或自动调用。字体沿系统中文字体，无新框架/字体网络依赖。

HTML只新增不带功能属性的布局div，并给CSS链接更新缓存版本。原文本、124个功能元素属性及表单归属一致，所有ID/必填/选项/隐藏状态/脚本URL一致；JS逐字节不变。范围与哈希证据见 [scope-check.json](scope-check.json)。保护基线在修改前对全部Git跟踪文件取SHA，产品修改后其他跟踪文件逐字节保持。此后仅追加本交接与本核验目录。

## 验证

- `.venv/bin/python -m pytest tests/integration/test_http.py -q`：5 passed，1条既有Starlette弃用警告。没有扩大全量后端回归。
- `git diff --check -- src/ontology_proj/interfaces/http/static`通过。全仓检查另发现原有test_business_extraction.py:298/test_ingestion.py:222文件末尾空行，本轮未修改这些文件。
- 深浅主题20组正文/次文字/链接/主按钮/错误配色均达到4.5:1，最低5.02；[contrast.json](contrast.json)。不将token计算当作全页面无障碍审计。
- 实际浏览器：1280桌面深浅工作台、查询页；375×812深色查询/录入/报告；812×375浅色已有证据横屏。375页面/报告和812证据页scrollWidth等于viewport width，无页面横向溢出；原表格/PDF保留局部滚动。
- 既有胜利桥精确查询实际返回3记录；点击原引用，原文/PDF第18页校验显示。既有报告f67b5e3b-aa8b-4f1f-8aab-f8a99904797d读取succeeded，下载链接可见；未新建报告。
- 保留原焦点轮廓、skip-link、表单标签、hidden优先级和reduced-motion；未实测屏幕阅读器/最大系统字体/全键盘流程，不声称完整无障碍认证。
- 未上传/解析/处理/重试/索引/生成问答/Agent；无新provider调用，未启动或重启服务。只读精确查询POST不写业务资料；用户原浏览器输入未操作，评估用独立临时页，临时viewport已恢复。

## 预览

[深色工作台](workspace-desktop-dark.jpg) · [浅色工作台](workspace-desktop-light.jpg) · [查询与已有证据](query-evidence-light.jpg) · [窄屏查询](query-mobile-dark.jpg) · [桌面报告入口](report-desktop-dark.jpg) · [窄屏已有报告](report-mobile-dark.jpg)。图片均实际浏览器截图，不是概念图。

ADR检查：无决策变化；沿用ADR-012同源原生网页与既有业务契约，不改PRD/spec行为。后续以用户视觉反馈决定布局微调，不自动扩大到功能改造。
