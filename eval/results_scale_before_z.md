# Eval at scale: the eval files plus generated extras

Run 2026-10-19T18:39:19 at commit b6a93f1. Corpus: 1095 files (1071 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Final vs at scale

The final run 2026-10-19T17:04:07 at commit 6d4c860; this run 2026-10-19T18:39:19 at commit b6a93f1.
The final run's divider numbers are those of its rerun at the 0.50 bar (`eval/results_divider.md`); the ranking is the final run's.


| half | queries | n | R@1 | R@5 | R@10 | MRR@10 | above the divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|
| Tuning half | all | 22 | 17 → 12 (77% → 54%) | 19 → 15 (86% → 68%) | 20 → 19 (91% → 86%) | 0.812 → 0.612 | 19.0 → 10.5 | 20 → 20 of 22 |
| Tuning half | private | 15 | 12 → 9 (80% → 60%) | 14 → 12 (93% → 80%) | 14 → 14 (93% → 93%) | 0.850 → 0.679 |  |  |
| Tuning half | public | 7 | 5 → 3 (71% → 43%) | 5 → 3 (71% → 43%) | 6 → 5 (86% → 71%) | 0.730 → 0.468 |  |  |
| Held-out half | all | 22 | 7 → 5 (32% → 23%) | 13 → 13 (59% → 59%) | 16 → 17 (73% → 77%) | 0.443 → 0.395 | 19.0 → 9.5 | 18 → 17 of 22 |
| Held-out half | private | 16 | 6 → 4 (38% → 25%) | 12 → 10 (75% → 62%) | 14 → 13 (88% → 81%) | 0.539 → 0.425 |  |  |
| Held-out half | public | 6 | 1 → 1 (17% → 17%) | 1 → 3 (17% → 50%) | 2 → 4 (33% → 67%) | 0.185 → 0.315 |  |  |
| Both halves | all | 44 | 24 → 17 (54% → 39%) | 32 → 28 (73% → 64%) | 36 → 36 (82% → 82%) | 0.627 → 0.503 | 19.0 → 10.0 | 38 → 37 of 44 |
| Both halves | private | 31 | 18 → 13 (58% → 42%) | 26 → 22 (84% → 71%) | 28 → 27 (90% → 87%) | 0.690 → 0.548 |  |  |
| Both halves | public | 13 | 6 → 4 (46% → 31%) | 6 → 6 (46% → 46%) | 8 → 9 (62% → 69%) | 0.479 → 0.398 |  |  |

Analysis (tuning half and generated set only, no tuning follows): the files that moved above correct ones are the evaluation's own screenshots, not the 1,000 new files. With 713 images instead of 63, CLIP's top hit clears the visual-query rule's strong-outlier bar (`clip_strong_z` 3.25, calibrated on 36 and 200 images) far more often, so the rule fires on text queries and halves BM25's weight: on the tuning half it fired on 7 of 9 meaning queries (0 of 9 without the extra files), 3 of 5 exact-word (0), 2 of 3 inside a scan (1) and 1 of 5 picture queries (1); on the generated set on 3 of 10 meaning (1), 2 of 8 inside a scan (1), 0 of 8 exact-word (0) and 4 of 6 picture queries (6). Open item z.

## Tuning half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 12/22 (54%) | 15/22 (68%) | 19/22 (86%) | 0.612 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 5 | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) | 1.000 |
| inside a scan | 3 | 0/3 (0%) | 2/3 (67%) | 3/3 (100%) | 0.278 |
| meaning | 9 | 5/9 (56%) | 6/9 (67%) | 8/9 (89%) | 0.613 |
| picture | 5 | 2/5 (40%) | 2/5 (40%) | 3/5 (60%) | 0.422 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 0/4 (0%) | 2/4 (50%) | 4/4 (100%) | 0.212 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 15 | 10/15 (67%) | 11/15 (73%) | 12/15 (80%) | 0.696 |
| text | 2 | 1/2 (50%) | 1/2 (50%) | 2/2 (100%) | 0.583 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 15 | 9/15 (60%) | 12/15 (80%) | 14/15 (93%) | 0.679 |
| public | 7 | 3/7 (43%) | 3/7 (43%) | 5/7 (71%) | 0.468 |

Weaker-matches divider (app): median 10.5 results above it; every correct file above it for 20 of 22 queries (private 8, 14 of 15; public 18, 6 of 7). Latency: p50 210 ms, p95 406 ms (engine stage timers).

Queries without a correct file at #1 (10). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| q01 | public | meaning | Find the image of a famous fractal formed by repeatedly applying a complex quadratic recurrence | 46 | no | dense #26 |  |
| q08 | public | picture | Locate the image whose color legend identifies different roots of a polynomial | 12 | yes | bm25 #29, dense #6 |  |
| q22 | private | picture | (private) | 47 | no | dense #28 |  |
| q26 | private | inside a scan | (private) | 3 | yes | bm25 #1, dense #1 |  |
| q27 | public | picture | Find the colorful image showing regions that attract complex-number iterations to different roots | 9 | yes | bm25 #31, dense #4 |  |
| q29 | private | meaning | (private) | 4 | yes | bm25 #1, dense #1 |  |
| q32 | public | meaning | why accuracy is useless for rare-event classification and PR-AUC preference | 6 | yes | bm25 #1, dense #1 |  |
| q33 | private | inside a scan | (private) | 3 | yes | bm25 #3, dense #1 |  |
| q40 | private | meaning | (private) | 10 | yes | bm25 #1, dense #1 |  |
| q41 | private | inside a scan | (private) | 6 | yes | bm25 #1, dense #2 |  |

## Held-out half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 5/22 (23%) | 13/22 (59%) | 17/22 (77%) | 0.395 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 4 | 2/4 (50%) | 3/4 (75%) | 3/4 (75%) | 0.625 |
| inside a scan | 3 | 0/3 (0%) | 1/3 (33%) | 2/3 (67%) | 0.222 |
| meaning | 10 | 2/10 (20%) | 5/10 (50%) | 8/10 (80%) | 0.336 |
| picture | 5 | 1/5 (20%) | 4/5 (80%) | 4/5 (80%) | 0.433 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 2/4 (50%) | 2/4 (50%) | 2/4 (50%) | 0.500 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 17 | 2/17 (12%) | 10/17 (59%) | 14/17 (82%) | 0.335 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 16 | 4/16 (25%) | 10/16 (62%) | 13/16 (81%) | 0.425 |
| public | 6 | 1/6 (17%) | 3/6 (50%) | 4/6 (67%) | 0.315 |

Weaker-matches divider (app): median 9.5 results above it; every correct file above it for 17 of 22 queries (private 9.5, 13 of 16; public 10.0, 4 of 6). Latency: p50 226 ms, p95 330 ms (engine stage timers).

## Both halves (44 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 44 | 17/44 (39%) | 28/44 (64%) | 36/44 (82%) | 0.503 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 9 | 7/9 (78%) | 8/9 (89%) | 8/9 (89%) | 0.833 |
| inside a scan | 6 | 0/6 (0%) | 3/6 (50%) | 5/6 (83%) | 0.250 |
| meaning | 19 | 7/19 (37%) | 11/19 (58%) | 16/19 (84%) | 0.467 |
| picture | 10 | 3/10 (30%) | 6/10 (60%) | 7/10 (70%) | 0.428 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 8 | 2/8 (25%) | 4/8 (50%) | 6/8 (75%) | 0.356 |
| photo | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |
| screenshot | 32 | 12/32 (38%) | 21/32 (66%) | 26/32 (81%) | 0.504 |
| text | 2 | 1/2 (50%) | 1/2 (50%) | 2/2 (100%) | 0.583 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 31 | 13/31 (42%) | 22/31 (71%) | 27/31 (87%) | 0.548 |
| public | 13 | 4/13 (31%) | 6/13 (46%) | 9/13 (69%) | 0.398 |

Weaker-matches divider (app): median 10.0 results above it; every correct file above it for 37 of 44 queries (private 9, 27 of 31; public 14, 10 of 13). Latency: p50 232 ms, p95 406 ms (engine stage timers).

