# Coverage queries (generated files)

Run 2026-10-20T01:20:25 at commit e33e85b. Corpus: 95 files (71 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Coverage queries (generated files) (21 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 21 | 18/21 (86%) | 21/21 (100%) | 21/21 (100%) | 0.929 |
| 1. BM25 only | 21 | 19/21 (90%) | 20/21 (95%) | 20/21 (95%) | 0.921 |
| 2. Dense only | 21 | 18/21 (86%) | 19/21 (90%) | 20/21 (95%) | 0.877 |
| 3. CLIP only | 21 | 2/21 (10%) | 3/21 (14%) | 3/21 (14%) | 0.119 |
| 4. BM25 + dense | 21 | 20/21 (95%) | 20/21 (95%) | 20/21 (95%) | 0.952 |
| 5. All three, CLIP ungated | 21 | 12/21 (57%) | 16/21 (76%) | 18/21 (86%) | 0.643 |
| 7. All three, gated, no every-word list | 21 | 17/21 (81%) | 21/21 (100%) | 21/21 (100%) | 0.905 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 4 | 3/4 (75%) | 4/4 (100%) | 4/4 (100%) | 0.875 |
| filtered | 7 | 7/7 (100%) | 7/7 (100%) | 7/7 (100%) | 1.000 |
| inside a scan | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| meaning | 3 | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | 1.000 |
| mixed | 6 | 4/6 (67%) | 6/6 (100%) | 6/6 (100%) | 0.833 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| doc | 4 | 4/4 (100%) | 4/4 (100%) | 4/4 (100%) | 1.000 |
| pdf | 8 | 6/8 (75%) | 8/8 (100%) | 8/8 (100%) | 0.875 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |
| slides | 6 | 5/6 (83%) | 6/6 (100%) | 6/6 (100%) | 0.917 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| public | 21 | 18/21 (86%) | 21/21 (100%) | 21/21 (100%) | 0.929 |

Weaker-matches divider (app): median 9 results above it; every correct file above it for 20 of 21 queries (public 9, 20 of 21). Latency: p50 102 ms, p95 146 ms (engine stage timers).

Queries without a correct file at #1 (3). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| c04 | public | exact word | singular values are the square roots of the eigenvalues of A transpose A | 2 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip – |
| c15 | public | mixed | train 12345 arrival time in Madgaon | 2 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip – |
| c20 | public | mixed | HACKATHON 2026 registration deadline and prize | 2 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip – |

