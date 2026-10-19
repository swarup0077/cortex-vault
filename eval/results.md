# Eval: final settings

Run 2026-10-19T17:04:07 at commit 6d4c860. Corpus: 95 files (71 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Baseline vs final

Baseline run 2026-10-19T13:10:31 at commit d8744d2; this run 2026-10-19T17:04:07 at commit 6d4c860. The divider by set wasn't recorded by the baseline.

| half | queries | n | R@1 | R@5 | R@10 | MRR@10 | above the divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|
| Tuning half | all | 22 | 16 → 17 (73% → 77%) | 19 → 19 (86% → 86%) | 19 → 20 (86% → 91%) | 0.774 → 0.812 | 14.5 → 40.0 | 19 → 22 of 22 |
| Tuning half | private | 15 | 12 → 12 (80% → 80%) | 14 → 14 (93% → 93%) | 14 → 14 (93% → 93%) | 0.856 → 0.850 |  |  |
| Tuning half | public | 7 | 4 → 5 (57% → 71%) | 5 → 5 (71% → 71%) | 5 → 6 (71% → 86%) | 0.600 → 0.730 |  |  |
| Held-out half | all | 22 | 8 → 7 (36% → 32%) | 13 → 13 (59% → 59%) | 17 → 16 (77% → 73%) | 0.464 → 0.443 | 17.0 → 37.0 | 17 → 21 of 22 |
| Held-out half | private | 16 | 7 → 6 (44% → 38%) | 12 → 12 (75% → 75%) | 15 → 14 (94% → 88%) | 0.569 → 0.539 |  |  |
| Held-out half | public | 6 | 1 → 1 (17% → 17%) | 1 → 1 (17% → 17%) | 2 → 2 (33% → 33%) | 0.183 → 0.185 |  |  |
| Both halves | all | 44 | 24 → 24 (54% → 54%) | 32 → 32 (73% → 73%) | 36 → 36 (82% → 82%) | 0.619 → 0.627 | 15.5 → 39.5 | 36 → 43 of 44 |
| Both halves | private | 31 | 19 → 18 (61% → 58%) | 26 → 26 (84% → 84%) | 29 → 28 (94% → 90%) | 0.708 → 0.690 |  |  |
| Both halves | public | 13 | 5 → 6 (38% → 46%) | 6 → 6 (46% → 46%) | 7 → 8 (54% → 62%) | 0.408 → 0.479 |  |  |

## Tuning half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 17/22 (77%) | 19/22 (86%) | 20/22 (91%) | 0.812 |
| 1. BM25 only | 22 | 14/22 (64%) | 15/22 (68%) | 16/22 (73%) | 0.657 |
| 2. Dense only | 22 | 13/22 (59%) | 16/22 (73%) | 19/22 (86%) | 0.654 |
| 3. CLIP only | 22 | 7/22 (32%) | 13/22 (59%) | 15/22 (68%) | 0.444 |
| 4. BM25 + dense | 22 | 14/22 (64%) | 16/22 (73%) | 19/22 (86%) | 0.688 |
| 5. All three, CLIP ungated | 22 | 14/22 (64%) | 16/22 (73%) | 18/22 (82%) | 0.686 |
| 7. All three, gated, no every-word list | 22 | 17/22 (77%) | 19/22 (86%) | 20/22 (91%) | 0.812 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 5 | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) | 1.000 |
| inside a scan | 3 | 1/3 (33%) | 3/3 (100%) | 3/3 (100%) | 0.583 |
| meaning | 9 | 8/9 (89%) | 8/9 (89%) | 8/9 (89%) | 0.889 |
| picture | 5 | 3/5 (60%) | 3/5 (60%) | 4/5 (80%) | 0.622 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 2/4 (50%) | 4/4 (100%) | 4/4 (100%) | 0.688 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 15 | 12/15 (80%) | 12/15 (80%) | 13/15 (87%) | 0.807 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 15 | 12/15 (80%) | 14/15 (93%) | 14/15 (93%) | 0.850 |
| public | 7 | 5/7 (71%) | 5/7 (71%) | 6/7 (86%) | 0.730 |

Weaker-matches divider (app): median 40.0 results above it; every correct file above it for 22 of 22 queries (private 33, 15 of 15; public 43, 7 of 7). Latency: p50 190 ms, p95 258 ms (engine stage timers).

Queries without a correct file at #1 (5). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| q01 | public | meaning | Find the image of a famous fractal formed by repeatedly applying a complex quadratic recurrence | 43 | yes | dense #24 | bm25 –, dense 24, clip 9 |
| q08 | public | picture | Locate the image whose color legend identifies different roots of a polynomial | 9 | yes | bm25 #29, dense #6 | bm25 29, dense 6, clip 8 |
| q22 | private | picture | (private) | 46 | yes | dense #26 | bm25 –, dense 26, clip 5 |
| q33 | private | inside a scan | (private) | 2 | yes | bm25 #3, dense #1 | bm25 3, dense 1, clip – |
| q41 | private | inside a scan | (private) | 4 | yes | bm25 #1, dense #2 | bm25 1, dense 2, clip – |

## Held-out half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 7/22 (32%) | 13/22 (59%) | 16/22 (73%) | 0.443 |
| 1. BM25 only | 22 | 7/22 (32%) | 9/22 (41%) | 9/22 (41%) | 0.364 |
| 2. Dense only | 22 | 5/22 (23%) | 11/22 (50%) | 15/22 (68%) | 0.330 |
| 3. CLIP only | 22 | 2/22 (9%) | 9/22 (41%) | 14/22 (64%) | 0.229 |
| 4. BM25 + dense | 22 | 7/22 (32%) | 9/22 (41%) | 12/22 (54%) | 0.373 |
| 5. All three, CLIP ungated | 22 | 5/22 (23%) | 14/22 (64%) | 16/22 (73%) | 0.373 |
| 7. All three, gated, no every-word list | 22 | 7/22 (32%) | 13/22 (59%) | 16/22 (73%) | 0.443 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 4 | 2/4 (50%) | 4/4 (100%) | 4/4 (100%) | 0.675 |
| inside a scan | 3 | 1/3 (33%) | 1/3 (33%) | 2/3 (67%) | 0.375 |
| meaning | 10 | 2/10 (20%) | 5/10 (50%) | 7/10 (70%) | 0.341 |
| picture | 5 | 2/5 (40%) | 3/5 (60%) | 3/5 (60%) | 0.500 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 3/4 (75%) | 4/4 (100%) | 4/4 (100%) | 0.800 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 17 | 3/17 (18%) | 8/17 (47%) | 11/17 (65%) | 0.326 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 16 | 6/16 (38%) | 12/16 (75%) | 14/16 (88%) | 0.539 |
| public | 6 | 1/6 (17%) | 1/6 (17%) | 2/6 (33%) | 0.185 |

Weaker-matches divider (app): median 37.0 results above it; every correct file above it for 21 of 22 queries (private 37.0, 16 of 16; public 34.5, 5 of 6). Latency: p50 174 ms, p95 243 ms (engine stage timers).

Queries without a correct file at #1 (15). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| q02 | private | meaning | (private) | 2 | yes | bm25 #1, dense #8 | bm25 1, dense 8, clip – |
| q05 | private | meaning | (private) | 11 | yes | bm25 #21, dense #7 | bm25 21, dense 7, clip 7 |
| q06 | public | picture | Search for the picture with a dark central shape surrounded by intricate purple branching boundaries | not in 50 | no | – | bm25 –, dense –, clip – |
| q07 | private | meaning | (private) | 2 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip 8 |
| q09 | private | meaning | (private) | 18 | yes | bm25 #15, dense #30 | bm25 15, dense 30, clip 9 |
| q10 | private | exact word | (private) | 2 | yes | bm25 #1, dense #3 | bm25 1, dense 3, clip 5 |
| q15 | public | picture | Which image has horizontal and vertical axes labeled with real and imaginary components? | 14 | yes | bm25 #32, dense #9 | bm25 31, dense 9, clip 4 |
| q16 | private | picture | (private) | 2 | yes | bm25 #25, dense #5, clip #2 | bm25 25, dense 5, clip 2 |
| q18 | private | exact word | (private) | 5 | yes | dense #12, clip #2 | bm25 –, dense 12, clip 2 |
| q19 | public | meaning | Find the plot showing regions of convergence for an iterative method applied to a cubic polynomial | 21 | yes | dense #8 | bm25 –, dense 8, clip 9 |
| q20 | private | inside a scan | (private) | 8 | yes | dense #15, clip #2 | bm25 –, dense 15, clip 2 |
| q21 | public | meaning | Search for the plot with a complex function involving a negative power of z | 9 | yes | bm25 #11, dense #5 | bm25 11, dense 5, clip 10 |
| q24 | public | inside a scan | Search for the image where the function label contains z raised to the third power | 12 | yes | bm25 #36, dense #5 | bm25 36, dense 5, clip 18 |
| q25 | private | meaning | (private) | 10 | yes | dense #16, clip #4 | bm25 –, dense 16, clip 4 |
| q37 | private | meaning | (private) | 5 | yes | bm25 #2, dense #3 | bm25 2, dense 3, clip – |

## Both halves (44 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 44 | 24/44 (54%) | 32/44 (73%) | 36/44 (82%) | 0.627 |
| 1. BM25 only | 44 | 21/44 (48%) | 24/44 (54%) | 25/44 (57%) | 0.510 |
| 2. Dense only | 44 | 18/44 (41%) | 27/44 (61%) | 34/44 (77%) | 0.492 |
| 3. CLIP only | 44 | 9/44 (20%) | 22/44 (50%) | 29/44 (66%) | 0.337 |
| 4. BM25 + dense | 44 | 21/44 (48%) | 25/44 (57%) | 31/44 (70%) | 0.530 |
| 5. All three, CLIP ungated | 44 | 19/44 (43%) | 30/44 (68%) | 34/44 (77%) | 0.530 |
| 7. All three, gated, no every-word list | 44 | 24/44 (54%) | 32/44 (73%) | 36/44 (82%) | 0.627 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 9 | 7/9 (78%) | 9/9 (100%) | 9/9 (100%) | 0.856 |
| inside a scan | 6 | 2/6 (33%) | 4/6 (67%) | 5/6 (83%) | 0.479 |
| meaning | 19 | 10/19 (53%) | 13/19 (68%) | 15/19 (79%) | 0.601 |
| picture | 10 | 5/10 (50%) | 6/10 (60%) | 7/10 (70%) | 0.561 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 8 | 5/8 (62%) | 8/8 (100%) | 8/8 (100%) | 0.744 |
| photo | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |
| screenshot | 32 | 15/32 (47%) | 20/32 (62%) | 24/32 (75%) | 0.551 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 31 | 18/31 (58%) | 26/31 (84%) | 28/31 (90%) | 0.690 |
| public | 13 | 6/13 (46%) | 6/13 (46%) | 8/13 (62%) | 0.479 |

Weaker-matches divider (app): median 39.5 results above it; every correct file above it for 43 of 44 queries (private 35, 31 of 31; public 43, 12 of 13). Latency: p50 188 ms, p95 258 ms (engine stage timers).

Queries without a correct file at #1 (20). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| q01 | public | meaning | Find the image of a famous fractal formed by repeatedly applying a complex quadratic recurrence | 43 | yes | dense #24 | bm25 –, dense 24, clip 9 |
| q02 | private | meaning | (private) | 2 | yes | bm25 #1, dense #8 | bm25 1, dense 8, clip – |
| q05 | private | meaning | (private) | 11 | yes | bm25 #21, dense #7 | bm25 21, dense 7, clip 7 |
| q06 | public | picture | Search for the picture with a dark central shape surrounded by intricate purple branching boundaries | not in 50 | no | – | bm25 –, dense –, clip – |
| q07 | private | meaning | (private) | 2 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip 8 |
| q08 | public | picture | Locate the image whose color legend identifies different roots of a polynomial | 9 | yes | bm25 #29, dense #6 | bm25 29, dense 6, clip 8 |
| q09 | private | meaning | (private) | 18 | yes | bm25 #15, dense #30 | bm25 15, dense 30, clip 9 |
| q10 | private | exact word | (private) | 2 | yes | bm25 #1, dense #3 | bm25 1, dense 3, clip 5 |
| q15 | public | picture | Which image has horizontal and vertical axes labeled with real and imaginary components? | 14 | yes | bm25 #32, dense #9 | bm25 31, dense 9, clip 4 |
| q16 | private | picture | (private) | 2 | yes | bm25 #25, dense #5, clip #2 | bm25 25, dense 5, clip 2 |
| q18 | private | exact word | (private) | 5 | yes | dense #12, clip #2 | bm25 –, dense 12, clip 2 |
| q19 | public | meaning | Find the plot showing regions of convergence for an iterative method applied to a cubic polynomial | 21 | yes | dense #8 | bm25 –, dense 8, clip 9 |
| q20 | private | inside a scan | (private) | 8 | yes | dense #15, clip #2 | bm25 –, dense 15, clip 2 |
| q21 | public | meaning | Search for the plot with a complex function involving a negative power of z | 9 | yes | bm25 #11, dense #5 | bm25 11, dense 5, clip 10 |
| q22 | private | picture | (private) | 46 | yes | dense #26 | bm25 –, dense 26, clip 5 |
| q24 | public | inside a scan | Search for the image where the function label contains z raised to the third power | 12 | yes | bm25 #36, dense #5 | bm25 36, dense 5, clip 18 |
| q25 | private | meaning | (private) | 10 | yes | dense #16, clip #4 | bm25 –, dense 16, clip 4 |
| q33 | private | inside a scan | (private) | 2 | yes | bm25 #3, dense #1 | bm25 3, dense 1, clip – |
| q37 | private | meaning | (private) | 5 | yes | bm25 #2, dense #3 | bm25 2, dense 3, clip – |
| q41 | private | inside a scan | (private) | 4 | yes | bm25 #1, dense #2 | bm25 1, dense 2, clip – |

