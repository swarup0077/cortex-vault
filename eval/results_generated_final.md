# Generated-file set: final settings

Run 2026-10-19T17:04:39 at commit 6d4c860. Corpus: 95 files (71 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Baseline vs final

Baseline run 2026-10-19T13:10:50 at commit d8744d2; this run 2026-10-19T17:04:39 at commit 6d4c860. The divider by set wasn't recorded by the baseline.

| half | queries | n | R@1 | R@5 | R@10 | MRR@10 | above the divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|
| Generated-file set | all | 32 | 29 → 28 (91% → 88%) | 32 → 32 (100% → 100%) | 32 → 32 (100% → 100%) | 0.953 → 0.927 | 15.5 → 23.0 | 32 → 32 of 32 |
| Generated-file set | public | 32 | 29 → 28 (91% → 88%) | 32 → 32 (100% → 100%) | 32 → 32 (100% → 100%) | 0.953 → 0.927 |  |  |

## Generated-file set (32 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 32 | 28/32 (88%) | 32/32 (100%) | 32/32 (100%) | 0.927 |
| 1. BM25 only | 32 | 23/32 (72%) | 25/32 (78%) | 26/32 (81%) | 0.755 |
| 2. Dense only | 32 | 21/32 (66%) | 26/32 (81%) | 26/32 (81%) | 0.716 |
| 3. CLIP only | 32 | 13/32 (41%) | 15/32 (47%) | 19/32 (59%) | 0.448 |
| 4. BM25 + dense | 32 | 24/32 (75%) | 26/32 (81%) | 26/32 (81%) | 0.781 |
| 5. All three, CLIP ungated | 32 | 21/32 (66%) | 25/32 (78%) | 26/32 (81%) | 0.702 |
| 7. All three, gated, no every-word list | 32 | 27/32 (84%) | 32/32 (100%) | 32/32 (100%) | 0.911 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.000 |
| inside a scan | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.000 |
| meaning | 10 | 6/10 (60%) | 10/10 (100%) | 10/10 (100%) | 0.767 |
| picture | 6 | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | 1.000 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| doc | 1 | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) | 0.500 |
| pdf | 8 | 6/8 (75%) | 8/8 (100%) | 8/8 (100%) | 0.854 |
| photo | 10 | 10/10 (100%) | 10/10 (100%) | 10/10 (100%) | 1.000 |
| screenshot | 10 | 9/10 (90%) | 10/10 (100%) | 10/10 (100%) | 0.933 |
| slides | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| public | 32 | 28/32 (88%) | 32/32 (100%) | 32/32 (100%) | 0.927 |

Weaker-matches divider (app): median 23.0 results above it; every correct file above it for 32 of 32 queries (public 23.0, 32 of 32). Latency: p50 177 ms, p95 296 ms (engine stage timers).

Queries without a correct file at #1 (4). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| g09 | public | meaning | the page that defines the singular value decomposition | 3 | yes | bm25 #2, dense #2 | bm25 2, dense 2, clip – |
| g12 | public | meaning | default hyperparameters for the Adam optimizer | 2 | yes | bm25 #6, dense #3 | bm25 6, dense 3, clip – |
| g14 | public | meaning | what was decided at the project sync about freezing the API | 2 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip – |
| g18 | public | meaning | advice on sleeping better and avoiding coffee | 3 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip 9 |

