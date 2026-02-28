"""
RAG (Retrieval-Augmented Generation) 子系统

模块:
  - embeddings: 向量化服务 (Provider + TF-IDF fallback)
  - chunker: 文档分块器 (代码感知 + 通用文本)
  - index: 向量索引 (numpy + SQLite 持久化)
  - retriever: 检索器 (向量 + 关键词 + 混合)
  - indexer: 后台索引器 (工作区自动扫描)
"""
from studio.backend.ai.rag.embeddings import EmbeddingService, get_embedding_service
from studio.backend.ai.rag.chunker import CodeChunker, TextChunker, Chunk
from studio.backend.ai.rag.index import VectorIndex, IndexEntry, get_vector_index
from studio.backend.ai.rag.retriever import RAGRetriever, RetrievalResult, get_retriever
from studio.backend.ai.rag.indexer import BackgroundIndexer, get_indexer

__all__ = [
    "EmbeddingService", "get_embedding_service",
    "CodeChunker", "TextChunker", "Chunk",
    "VectorIndex", "IndexEntry", "get_vector_index",
    "RAGRetriever", "RetrievalResult", "get_retriever",
    "BackgroundIndexer", "get_indexer",
]
