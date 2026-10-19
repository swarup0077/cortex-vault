# Eval baseline

Run 2026-10-19T13:10:31 at commit d8744d2. Corpus: 95 files (71 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Tuning half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 16/22 (73%) | 19/22 (86%) | 19/22 (86%) | 0.774 |
| 1. BM25 only | 22 | 14/22 (64%) | 15/22 (68%) | 16/22 (73%) | 0.657 |
| 2. Dense only | 22 | 13/22 (59%) | 15/22 (68%) | 17/22 (77%) | 0.634 |
| 3. CLIP only | 22 | 7/22 (32%) | 13/22 (59%) | 15/22 (68%) | 0.444 |
| 4. BM25 + dense | 22 | 13/22 (59%) | 16/22 (73%) | 17/22 (77%) | 0.647 |
| 5. All three, CLIP ungated | 22 | 16/22 (73%) | 17/22 (77%) | 19/22 (86%) | 0.762 |
| 7. All three, gated, no every-word list | 22 | 16/22 (73%) | 19/22 (86%) | 19/22 (86%) | 0.774 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 5 | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) | 1.000 |
| inside a scan | 3 | 1/3 (33%) | 3/3 (100%) | 3/3 (100%) | 0.611 |
| meaning | 9 | 8/9 (89%) | 8/9 (89%) | 8/9 (89%) | 0.889 |
| picture | 5 | 2/5 (40%) | 3/5 (60%) | 3/5 (60%) | 0.440 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 2/4 (50%) | 4/4 (100%) | 4/4 (100%) | 0.708 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 15 | 11/15 (73%) | 12/15 (80%) | 12/15 (80%) | 0.747 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 15 | 12/15 (80%) | 14/15 (93%) | 14/15 (93%) | 0.856 |
| public | 7 | 4/7 (57%) | 5/7 (71%) | 5/7 (71%) | 0.600 |

Weaker-matches divider (app): median 14.5 results above it; every correct file above it for 19 of 22 queries. Latency: p50 144 ms, p95 335 ms (engine stage timers).

Queries without a correct file at #1 (6):

| id | set | search type | query | rank | found by (app) | single-method rank |
|---|---|---|---|---|---|---|
| q01 | public | meaning | Find the image of a famous fractal formed by repeatedly applying a complex quadratic recurrence | 37 | clip #9 | bm25 –, dense –, clip 9 |
| q08 | public | picture | Locate the image whose color legend identifies different roots of a polynomial | 36 | clip #8 | bm25 –, dense –, clip 8 |
| q22 | private | picture | (private) | 35 | clip #5 | bm25 –, dense –, clip 5 |
| q27 | public | picture | Find the colorful image showing regions that attract complex-number iterations to different roots | 5 | clip #2 | bm25 –, dense –, clip 2 |
| q33 | private | inside a scan | (private) | 2 | bm25 #3, dense #1 | bm25 3, dense 1, clip – |
| q41 | private | inside a scan | (private) | 3 | bm25 #1, dense #2 | bm25 1, dense 2, clip – |

## Held-out half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 8/22 (36%) | 13/22 (59%) | 17/22 (77%) | 0.464 |
| 1. BM25 only | 22 | 7/22 (32%) | 9/22 (41%) | 9/22 (41%) | 0.364 |
| 2. Dense only | 22 | 5/22 (23%) | 11/22 (50%) | 14/22 (64%) | 0.324 |
| 3. CLIP only | 22 | 2/22 (9%) | 9/22 (41%) | 14/22 (64%) | 0.229 |
| 4. BM25 + dense | 22 | 7/22 (32%) | 9/22 (41%) | 13/22 (59%) | 0.378 |
| 5. All three, CLIP ungated | 22 | 7/22 (32%) | 15/22 (68%) | 19/22 (86%) | 0.452 |
| 7. All three, gated, no every-word list | 22 | 8/22 (36%) | 13/22 (59%) | 17/22 (77%) | 0.464 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 4 | 3/4 (75%) | 3/4 (75%) | 4/4 (100%) | 0.781 |
| inside a scan | 3 | 1/3 (33%) | 2/3 (67%) | 3/3 (100%) | 0.433 |
| meaning | 10 | 2/10 (20%) | 5/10 (50%) | 7/10 (70%) | 0.353 |
| picture | 5 | 2/5 (40%) | 3/5 (60%) | 3/5 (60%) | 0.450 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 3/4 (75%) | 4/4 (100%) | 4/4 (100%) | 0.833 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 17 | 4/17 (24%) | 8/17 (47%) | 12/17 (71%) | 0.346 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 16 | 7/16 (44%) | 12/16 (75%) | 15/16 (94%) | 0.569 |
| public | 6 | 1/6 (17%) | 1/6 (17%) | 2/6 (33%) | 0.183 |

Weaker-matches divider (app): median 17.0 results above it; every correct file above it for 17 of 22 queries. Latency: p50 166 ms, p95 266 ms (engine stage timers).

## Both halves (44 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 44 | 24/44 (54%) | 32/44 (73%) | 36/44 (82%) | 0.619 |
| 1. BM25 only | 44 | 21/44 (48%) | 24/44 (54%) | 25/44 (57%) | 0.510 |
| 2. Dense only | 44 | 18/44 (41%) | 26/44 (59%) | 31/44 (70%) | 0.479 |
| 3. CLIP only | 44 | 9/44 (20%) | 22/44 (50%) | 29/44 (66%) | 0.337 |
| 4. BM25 + dense | 44 | 20/44 (46%) | 25/44 (57%) | 30/44 (68%) | 0.512 |
| 5. All three, CLIP ungated | 44 | 23/44 (52%) | 32/44 (73%) | 38/44 (86%) | 0.607 |
| 7. All three, gated, no every-word list | 44 | 24/44 (54%) | 32/44 (73%) | 36/44 (82%) | 0.619 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 9 | 8/9 (89%) | 8/9 (89%) | 9/9 (100%) | 0.903 |
| inside a scan | 6 | 2/6 (33%) | 5/6 (83%) | 6/6 (100%) | 0.522 |
| meaning | 19 | 10/19 (53%) | 13/19 (68%) | 15/19 (79%) | 0.607 |
| picture | 10 | 4/10 (40%) | 6/10 (60%) | 6/10 (60%) | 0.445 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 8 | 5/8 (62%) | 8/8 (100%) | 8/8 (100%) | 0.771 |
| photo | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |
| screenshot | 32 | 15/32 (47%) | 20/32 (62%) | 24/32 (75%) | 0.534 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 31 | 19/31 (61%) | 26/31 (84%) | 29/31 (94%) | 0.708 |
| public | 13 | 5/13 (38%) | 6/13 (46%) | 7/13 (54%) | 0.408 |

Weaker-matches divider (app): median 15.5 results above it; every correct file above it for 36 of 44 queries. Latency: p50 154 ms, p95 335 ms (engine stage timers).

