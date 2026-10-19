# Generated-file set

Run 2026-10-19T18:39:35 at commit b6a93f1. Corpus: 1095 files (1071 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Final vs at scale

The final run 2026-10-19T17:04:39 at commit 6d4c860; this run 2026-10-19T18:39:35 at commit b6a93f1.
The final run's divider numbers are those of its rerun at the 0.50 bar (`eval/results_divider.md`); the ranking is the final run's.


| half | queries | n | R@1 | R@5 | R@10 | MRR@10 | above the divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|
| Generated-file set | all | 32 | 28 → 26 (88% → 81%) | 32 → 29 (100% → 91%) | 32 → 31 (100% → 97%) | 0.927 → 0.855 | 9.5 → 4.5 | 32 → 31 of 32 |
| Generated-file set | public | 32 | 28 → 26 (88% → 81%) | 32 → 29 (100% → 91%) | 32 → 31 (100% → 97%) | 0.927 → 0.855 |  |  |

## Generated-file set (32 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 32 | 26/32 (81%) | 29/32 (91%) | 31/32 (97%) | 0.855 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.000 |
| inside a scan | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.000 |
| meaning | 10 | 5/10 (50%) | 8/10 (80%) | 10/10 (100%) | 0.635 |
| picture | 6 | 5/6 (83%) | 5/6 (83%) | 5/6 (83%) | 0.833 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| doc | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| pdf | 8 | 5/8 (62%) | 7/8 (88%) | 8/8 (100%) | 0.742 |
| photo | 10 | 9/10 (90%) | 9/10 (90%) | 9/10 (90%) | 0.900 |
| screenshot | 10 | 9/10 (90%) | 10/10 (100%) | 10/10 (100%) | 0.925 |
| slides | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| text | 2 | 1/2 (50%) | 1/2 (50%) | 2/2 (100%) | 0.583 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| public | 32 | 26/32 (81%) | 29/32 (91%) | 31/32 (97%) | 0.855 |

Weaker-matches divider (app): median 4.5 results above it; every correct file above it for 31 of 32 queries (public 4.5, 31 of 32). Latency: p50 147 ms, p95 216 ms (engine stage timers).

Queries without a correct file at #1 (6). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| g09 | public | meaning | the page that defines the singular value decomposition | 10 | no | bm25 #2, dense #2 |  |
| g10 | public | meaning | paper on generating images by gradually removing noise | 3 | yes | bm25 #1, dense #2 |  |
| g12 | public | meaning | default hyperparameters for the Adam optimizer | 2 | yes | bm25 #6, dense #3 |  |
| g15 | public | meaning | how long to bake bread in a covered pot | 6 | yes | bm25 #1, dense #1 |  |
| g18 | public | meaning | advice on sleeping better and avoiding coffee | 4 | yes | bm25 #1, dense #1 |  |
| g24 | public | picture | the moon at night | 35 | yes | clip #1 |  |

