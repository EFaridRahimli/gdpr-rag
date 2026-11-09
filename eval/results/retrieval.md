| mode | embedding model | recall@5 | recall@10 | MRR |
|---|---|---|---|---|
| bm25 | - | 0.18 | 0.28 | 0.16 |
| vector | qwen/qwen3-embedding-8b | 0.86 | 0.94 | 0.72 |
| hybrid | qwen/qwen3-embedding-8b | 0.68 | 0.94 | 0.43 |
| llm-rerank | qwen/qwen3-embedding-8b | 0.96 | 0.96 | 0.94 |
| local-rerank | qwen/qwen3-embedding-8b | 0.28 | 0.56 | 0.18 |
| vector | mongodb/voyage-4-large | 0.92 | 1.00 | 0.77 |
| hybrid | mongodb/voyage-4-large | 0.68 | 0.92 | 0.41 |
| llm-rerank | mongodb/voyage-4-large | 1.00 | 1.00 | 0.97 |
| local-rerank | mongodb/voyage-4-large | 0.24 | 0.54 | 0.17 |
| vector | google/gemini-embedding-2 | 0.98 | 1.00 | 0.78 |
| hybrid | google/gemini-embedding-2 | 0.68 | 0.96 | 0.44 |
| llm-rerank | google/gemini-embedding-2 | 1.00 | 1.00 | 0.96 |
| local-rerank | google/gemini-embedding-2 | 0.24 | 0.58 | 0.18 |
| vector | openai/text-embedding-3-large | 0.84 | 0.90 | 0.62 |
| hybrid | openai/text-embedding-3-large | 0.66 | 0.88 | 0.36 |
| llm-rerank | openai/text-embedding-3-large | 0.96 | 0.96 | 0.92 |
| local-rerank | openai/text-embedding-3-large | 0.24 | 0.48 | 0.16 |
