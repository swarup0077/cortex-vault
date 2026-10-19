# Generated-file set: baseline

Run 2026-10-19T13:10:50 at commit d8744d2. Corpus: 95 files (71 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Generated-file set (32 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 32 | 29/32 (91%) | 32/32 (100%) | 32/32 (100%) | 0.953 |
| 1. BM25 only | 32 | 23/32 (72%) | 25/32 (78%) | 26/32 (81%) | 0.755 |
| 2. Dense only | 32 | 21/32 (66%) | 26/32 (81%) | 26/32 (81%) | 0.716 |
| 3. CLIP only | 32 | 13/32 (41%) | 15/32 (47%) | 19/32 (59%) | 0.448 |
| 4. BM25 + dense | 32 | 24/32 (75%) | 26/32 (81%) | 26/32 (81%) | 0.781 |
| 5. All three, CLIP ungated | 32 | 22/32 (69%) | 29/32 (91%) | 31/32 (97%) | 0.780 |
| 7. All three, gated, no every-word list | 32 | 28/32 (88%) | 32/32 (100%) | 32/32 (100%) | 0.938 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.000 |
| inside a scan | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.000 |
| meaning | 10 | 7/10 (70%) | 10/10 (100%) | 10/10 (100%) | 0.850 |
| picture | 6 | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | 1.000 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| doc | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| pdf | 8 | 6/8 (75%) | 8/8 (100%) | 8/8 (100%) | 0.875 |
| photo | 10 | 10/10 (100%) | 10/10 (100%) | 10/10 (100%) | 1.000 |
| screenshot | 10 | 9/10 (90%) | 10/10 (100%) | 10/10 (100%) | 0.950 |
| slides | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| public | 32 | 29/32 (91%) | 32/32 (100%) | 32/32 (100%) | 0.953 |

Weaker-matches divider (app): median 15.5 results above it; every correct file above it for 32 of 32 queries. Latency: p50 78 ms, p95 120 ms (engine stage timers).

Queries without a correct file at #1 (3):

| id | set | search type | query | rank | found by (app) | single-method rank |
|---|---|---|---|---|---|---|
| g09 | public | meaning | the page that defines the singular value decomposition | 2 | bm25 #2, dense #2 | bm25 2, dense 2, clip – |
| g12 | public | meaning | default hyperparameters for the Adam optimizer | 2 | bm25 #6, dense #3 | bm25 6, dense 3, clip – |
| g18 | public | meaning | advice on sleeping better and avoiding coffee | 2 | bm25 #1, dense #1 | bm25 1, dense 1, clip 9 |

