# Generated-file set

Run 2026-10-19T23:39:28 at commit ef51e68. Corpus: 1095 files (1071 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Generated-file set (32 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 32 | 26/32 (81%) | 31/32 (97%) | 31/32 (97%) | 0.875 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.000 |
| inside a scan | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.000 |
| meaning | 10 | 6/10 (60%) | 10/10 (100%) | 10/10 (100%) | 0.750 |
| picture | 6 | 4/6 (67%) | 5/6 (83%) | 5/6 (83%) | 0.750 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| doc | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| pdf | 8 | 5/8 (62%) | 8/8 (100%) | 8/8 (100%) | 0.771 |
| photo | 10 | 8/10 (80%) | 9/10 (90%) | 9/10 (90%) | 0.850 |
| screenshot | 10 | 10/10 (100%) | 10/10 (100%) | 10/10 (100%) | 1.000 |
| slides | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| text | 2 | 1/2 (50%) | 2/2 (100%) | 2/2 (100%) | 0.667 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| public | 32 | 26/32 (81%) | 31/32 (97%) | 31/32 (97%) | 0.875 |

Weaker-matches divider (app): median 4.5 results above it; every correct file above it for 32 of 32 queries (public 4.5, 32 of 32). Latency: p50 234 ms, p95 439 ms (engine stage timers).

Queries without a correct file at #1 (6). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| g09 | public | meaning | the page that defines the singular value decomposition | 3 | yes | bm25 #2, dense #2 |  |
| g10 | public | meaning | paper on generating images by gradually removing noise | 3 | yes | bm25 #1, dense #2 |  |
| g12 | public | meaning | default hyperparameters for the Adam optimizer | 2 | yes | bm25 #6, dense #3 |  |
| g15 | public | meaning | how long to bake bread in a covered pot | 3 | yes | bm25 #1, dense #1 |  |
| g21 | public | picture | snow covered mountains | 2 | yes | clip #1 |  |
| g24 | public | picture | the moon at night | 35 | yes | clip #1 |  |

