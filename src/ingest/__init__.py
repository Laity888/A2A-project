"""工单 02：RAGFlow 官方中文文档 → Markdown → 分层父子切分 → BGE-M3 hybrid → Milvus。

与示例项目 edu_rag-main 的 schema/切分/索引对齐（票 04「出入以示例为准」），
并补 source_url / category 元数据。所有参数来自 config（common.yaml + agents/retrieval.yaml
+ ingest.yaml），代码里不硬编码。重依赖（torch / milvus / langchain）在函数内延迟导入，
便于离线单元测试（票 10 §2）。
"""
