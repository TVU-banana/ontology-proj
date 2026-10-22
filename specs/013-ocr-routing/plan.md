# Spec013 OCR 接入计划与契约

2026-10-13，用户授权一次完成 RapidOCR + ONNX Runtime + PP-OCR 接入。

## 范围

仅解析层、配置、结果诊断与相关持久化/文档/验证。不修改业务规则，不覆盖旧解析，不运行生成/嵌入/图登记或现有服务。

## 配置与路由

DOCLING_OCR_MODE=auto（默认）/off；DOCLING_OCR_FORCE_PAGES 为1起点物理页列表（JSON数组），off不能带强制页。DOCLING_OCR_MODELS_PATH显式本地目录；不隐式扫描实验路径。模式auto使用Docling PDF_AWARE_LAYOUT_REGIONS，候选中排除picture/chart标签，并跳过有原生文字且无位图的区域（防止表格边框/填充误触发）；指定页强制整页OCR，允许照片文字的显式补识别。坏文字层不自动重跑/猜测质量，指定页强制；未检测到候选的扫描区域有漏判可能。不能以转换success作为识别质量证明。

PP-OCRv6 small检测/识别及配套方向分类权重固定SHA-256，RapidOCR3.10.0 + ONNX Runtime1.23.2。引擎仅本地CPU，不自动下载/外部插件。auto配置不完整时明确不可用；off不要求OCR资源。OCR模型初始化不等于推理；skip仅表示没有调用OCR reader。

## 输出与兼容

ParseOutcome新增可选ocr；原生Docling JSON不改。normalized-0.1新增可选ocr扩展，含版本、模式、强制页、每页区域/推理调用数/OCR文字数量/耗时/路由原因。page_no从1起，bbox为左上角归一化。局部OCR沿用Docling文字合并策略与项目证据位置适配；强制整页由Docling按模式优先OCR文字。diagnostics进入操作与normalized；失败不发布完整解析。旧快照读取保持，显式新配置失败重试遵循既有配置冲突规则，已发布快照不自动升级。

## 顺序与验证

T01 冻结预期；T02 固定依赖/预检；T03 路由与配置接入；T04 诊断保存；T05 单元与全量离线回归；T06 新隔离synthetic PDF实际OCR/skip/混合/空白/照片/强制路径，禁网验证并保存证据。首次失败保留，不重跑旧成功实验。
