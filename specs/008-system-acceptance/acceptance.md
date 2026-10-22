# PRD 验收证据与 S5.2 缺口

2026-10-12盘点并补[实际系统证据](results/system-20261012-01/review.md)及真实PDF本地解析/人工事实确认。历史结果只引用，不重跑或改写；连续稳定性素材为synthetic隔离副本，真实素材已在四登记对象/三面积子集跑三模块，完整S5.2与专业系统验收缺口保留。

| 项 | 已有证据及边界 | S5.2 新证据或缺口 |
|---|---|---|
| AC-01 持久文件 | [S2.3](../002-ingestion-foundation/results/s2.3/review.md)、[S5.1](../007-agent-web/results/review.md)本机文件/HTTP重启 | 新121轮、专用图重启、3新进程持久性通过；非跨机/生产故障域 |
| AC-02 去重 | S2.3 图/文件/HTTP；S5.1网页重复上传 | 新重复上传/同key重放/异载荷409/重复process通过，0新解析/抽取 |
| AC-03 复杂真实解析 | 旧素材为简单 synthetic，不能计通过 | 新60页转换及F01–F12人工基线；[Agent校对](results/proofread-20261012-01/review.md)67行402字段发现2个标准差串值，表头/续表/公式/OCR缺口仍保留；实际业务/专业系统验收未通过 |
| AC-04 Schema/限定 | [S2.2](../002-ingestion-foundation/results/s2.2/stability-followup.md)、S2.3真实抽取与离线反例 | 核心源码/Schema冻结；新持续范围保留3面积/单位/未知日期/pending，未新抽取 |
| AC-05 观测历史 | [S3.1](../003-query-evidence/results/review.md)明确 synthetic 两日期机制、来源隔离 | 新事实/旧文件保护与文档局部身份隔离通过；不计真实历史 |
| AC-06 多证据/跨文档 | S3.1实际证据与明确机制 fixture | 新实际图/HTTP跨来源30项通过，18引用各自定位；两份synthetic变体 |
| AC-07 位置 | S3.1 TCP/浏览器坐标和 agent 检视 | 旧121轮和3进程通过；真实PDF9探针/两页Agent框检查；人工已确认事实的页/来源预期，所有实际引用/bbox系统核验仍未完成，5公式位置明确不可用 |
| AC-08 检索职责 | [S3.2](../004-semantic-context/results/s3.2/review.md)真实索引与图 | 新嵌入拒绝degraded但2精确构件保留、恢复ready；既有向量复用 |
| AC-09 有据问答 | [S3.3](../005-cited-answer/results/review.md)真实provider六题，S5.1网页新增问答 | 新5问首次通过、真实依赖拒绝无伪造回答及新预算内请求恢复；非整体准确率 |
| AC-10 固定报告 | [S4.1](../006-observation-report/results/review.md)真实图/文件/TCP | 新10正常报告+2故障fixture、单次登记/原字节重试/重启读取通过 |
| AC-11 占位 | S4.1/S5.1 明确 not_run 与演示分流 | 新报告持续保留not_run、synthetic；专业审核仍未运行 |
| AC-12 写入保障 | S2.3/S4.1真实事务、显式故障注入、恢复 | 新实际图停/启、图不可用启动延迟恢复、interrupted/显式retry原字节单次登记通过 |
| AC-13 架构 | 既有 AST/无SDK/替身替换、HTTP/Agent共享约束 | 准备轮2项实际AST/无SDK检查；核心代码不变沿用旧替换证据，真实新角色工具和依赖故障走共享用例，不宣称全部适配器已实际替换 |
| AC-14 证据分级 | 各阶段review明确 synthetic/真实provider/替身 | 新review/trace/freeze/进程/服务清单/交付明确；专业人工/复杂真实缺口保留 |

原 spec §20 Done 要求三个模块用选定真实素材实际运行、证据位置人工核查；当前真实子集已运行，完整格式覆盖与专业位置验收仍缺。用户确认先完成可执行技术部分，未改变原 Done；不会以“有未验证清单”替代必需证据。

本轮[真实API结果](results/real-shengli-20261012-01/review.md)：用户已批准指定API，新全文请求保留3045块/34表。实际一次55.156秒、18k输出截断、程序拒绝；没有业务发布、图/索引/问答/报告，AC-04/08/09/10真实链路缺口不计通过。新输入表示及来源风险隔离见ADR-014；专业验收/OCR不变。

## 真实受控链路补证（2026-10-12最新状态）

[实际结果](results/real-shengli-revalidated-20261012-01/review.md)/[86项独立对照](results/real-shengli-revalidated-20261012-01/independent-comparison.json)覆盖AC-04/08/09/10真实子集技术链路：三观测、实际本机嵌入、5真实API问答、固定五章报告；AC-07补47业务引用API及第14/18页浏览器Agent核看，仍非用户专业位置验收。AC-03既有实际复杂表的三条面积正确，但全表续接/标准差/OCR/公式问题未解决，不可勾选完整解析通过。AC-11报告仍not_run、业务accepted/unreviewed；AC-13新增登记纯领域与bootstrap共用，157相关离线测试。新wheel278文件逐字节与源码一致，复用本机依赖，不宣称新跨机全依赖安装。

T12/完整S5.2仍待专业终审和原复杂格式证据缺口裁定；真实历史尚无素材，只承认已有synthetic机制证据，不静默新增必需两报告门槛，也不称真实历史已验收。原完整Done不变。旧表中的“未新抽取/实际业务未通过”等是此前证据快照，以本段及实际结果更新为准。

## 最终裁定（覆盖前述待终审状态）

用户本轮看过原报告/生成汇总反馈无问题，Agent重新查看完整原页及88项保存产物复核通过。[最终矩阵裁定](results/final-review-20261012-01/review.md)/[用户记录](results/final-review-20261012-01/human-confirmation.json)将AC-03/07代表性真实范围与失败区域解释补齐，T12和S5.2完成。原Done不要求所有候选复杂格式成功，明确允许实际失败/不支持解释与标注synthetic历史；保留OCR等限制而非将其误称已成功。不是全文/生产认证；产品内not_run不改。
