| mode | embedding model | recall@5 | recall@10 | MRR |
|---|---|---|---|---|
| bm25 | - | 0.26 | 0.42 | 0.17 |
| vector | qwen/qwen3-embedding-8b | 0.86 | 0.94 | 0.72 |
| hybrid | qwen/qwen3-embedding-8b | 0.68 | 0.86 | 0.46 |
| llm-rerank | qwen/qwen3-embedding-8b | 0.96 | 0.96 | 0.94 |
| vector | mongodb/voyage-4-large | 0.92 | 1.00 | 0.77 |
| hybrid | mongodb/voyage-4-large | 0.70 | 0.90 | 0.45 |
| llm-rerank | mongodb/voyage-4-large | 1.00 | 1.00 | 0.95 |
| vector | google/gemini-embedding-2 | 0.98 | 1.00 | 0.78 |
| hybrid | google/gemini-embedding-2 | 0.66 | 0.92 | 0.49 |
| llm-rerank | google/gemini-embedding-2 | 1.00 | 1.00 | 0.96 |
| vector | openai/text-embedding-3-large | 0.84 | 0.90 | 0.61 |
| hybrid | openai/text-embedding-3-large | 0.58 | 0.86 | 0.38 |
| llm-rerank | openai/text-embedding-3-large | 0.98 | 0.98 | 0.92 |
| vector | local:nomic-ai/nomic-embed-text-v1.5-Q | 0.24 | 0.30 | 0.10 |
| hybrid | local:nomic-ai/nomic-embed-text-v1.5-Q | 0.32 | 0.42 | 0.21 |
| llm-rerank | local:nomic-ai/nomic-embed-text-v1.5-Q | 0.62 | 0.62 | 0.57 |
| vector | local:snowflake/snowflake-arctic-embed-m | 0.28 | 0.42 | 0.19 |
| hybrid | local:snowflake/snowflake-arctic-embed-m | 0.30 | 0.46 | 0.24 |
| llm-rerank | local:snowflake/snowflake-arctic-embed-m | 0.72 | 0.72 | 0.70 |
| vector | local:google/embeddinggemma-300m | 0.56 | 0.66 | 0.39 |
| hybrid | local:google/embeddinggemma-300m | 0.50 | 0.66 | 0.35 |
| llm-rerank | local:google/embeddinggemma-300m | 0.76 | 0.76 | 0.74 |
| vector | local:Qwen/Qwen3-Embedding-0.6B-Q | 0.44 | 0.64 | 0.34 |
| hybrid | local:Qwen/Qwen3-Embedding-0.6B-Q | 0.40 | 0.62 | 0.30 |
| llm-rerank | local:Qwen/Qwen3-Embedding-0.6B-Q | 0.82 | 0.82 | 0.79 |
