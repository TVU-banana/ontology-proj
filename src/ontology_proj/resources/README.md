# 运行时资源

`business.schema.json` 是 [主维护 Schema](../../../specs/001-business-extraction/business.schema.json) 的逐字节打包副本，供安装后的应用读取；不单独设计或修改。主 Schema 改动时重新生成副本并通过 `test_schema_packaging_matches_primary_contract`，避免运行时访问仓库的 specs/ 或 experiments/。

`extraction-prompt.txt` 维护模型抽取规则，配置记录其 SHA-256。修改提示后必须新建独立验收基线，不覆盖已冻结请求。提示不包含 golden、人工预期或前轮回答。
