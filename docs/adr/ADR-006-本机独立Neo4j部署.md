# ADR-006 本机独立 Neo4j 部署

- 状态：已接受
- 记录日期：2026-10-11
- 决定依据：用户确认项目专用 Docker 容器和持久卷；版本与端口为授权范围内的实施选择
- 适用范围：S2.3 本机图存储与后续 MVP
- 替代关系：无

## 背景
应用已确定本机运行，但正式图服务尚未部署；本机同时运行多项其他 Docker 服务。

## 决定与理由
使用独立 Neo4j Community 容器、命名持久卷和仅绑定 loopback 的端口。实际镜像为 ARM64 Neo4j 5.26.31，固定镜像摘要；Python 驱动锁定 6.4.0。应用保持本机、单进程运行，不复用其他项目数据。

## 影响与边界
容器停止/重建保留命名卷；更新镜像须另行验证，不能只改浮动标签。凭据位于忽略文件，配置示例不包含实际凭据。这是本机演示部署，不代表备份、跨主机容灾或生产多租户隔离。

## 规范与验证入口
[部署配置](../../deploy/compose.yaml)、[spec](../../spec.md#9-neo4j-模型写入与索引)、[S2.3 计划](../../specs/002-ingestion-foundation/plan.md#10-s23-正式图登记与录入闭环)。实际验收结论由任务与交接维护。

实现依据：[官方 Docker 部署](https://neo4j.com/docs/operations-manual/current/docker/introduction/)、[驱动兼容性](https://neo4j.com/docs/python-manual/current/install/)、[事务规则](https://neo4j.com/docs/python-manual/current/transactions/)。事务中只执行图操作，模型/文件不进入事务回调。
