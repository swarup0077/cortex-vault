# Eval at scale: the eval files plus generated extras

Run 2026-10-19T23:39:13 at commit ef51e68. Corpus: 1095 files (1071 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

## Post-z vs at scale

The post-z run 2026-10-19T23:38:29 at commit ef51e68; this run 2026-10-19T23:39:13 at commit ef51e68.

| half | queries | n | R@1 | R@5 | R@10 | MRR@10 | above the divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|
| Tuning half | all | 22 | 17 → 16 (77% → 73%) | 18 → 17 (82% → 77%) | 19 → 19 (86% → 86%) | 0.801 → 0.753 | 19.5 → 17.5 | 20 → 20 of 22 |
| Tuning half | private | 15 | 13 → 13 (87% → 87%) | 14 → 14 (93% → 93%) | 14 → 14 (93% → 93%) | 0.900 → 0.889 |  |  |
| Tuning half | public | 7 | 4 → 3 (57% → 43%) | 4 → 3 (57% → 43%) | 5 → 5 (71% → 71%) | 0.587 → 0.462 |  |  |
| Held-out half | all | 22 | 6 → 9 (27% → 41%) | 12 → 12 (54% → 54%) | 16 → 14 (73% → 64%) | 0.402 → 0.466 | 19.5 → 21.5 | 18 → 19 of 22 |
| Held-out half | private | 16 | 5 → 6 (31% → 38%) | 11 → 9 (69% → 56%) | 14 → 11 (88% → 69%) | 0.483 → 0.453 |  |  |
| Held-out half | public | 6 | 1 → 3 (17% → 50%) | 1 → 3 (17% → 50%) | 2 → 3 (33% → 50%) | 0.185 → 0.500 |  |  |
| Both halves | all | 44 | 23 → 25 (52% → 57%) | 30 → 29 (68% → 66%) | 35 → 33 (80% → 75%) | 0.601 → 0.610 | 19.5 → 19.0 | 38 → 39 of 44 |
| Both halves | private | 31 | 18 → 19 (58% → 61%) | 25 → 23 (81% → 74%) | 28 → 25 (90% → 81%) | 0.685 → 0.664 |  |  |
| Both halves | public | 13 | 5 → 6 (38% → 46%) | 5 → 6 (38% → 46%) | 7 → 8 (54% → 62%) | 0.402 → 0.480 |  |  |

## Tuning half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 16/22 (73%) | 17/22 (77%) | 19/22 (86%) | 0.753 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 5 | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) | 1.000 |
| inside a scan | 3 | 2/3 (67%) | 3/3 (100%) | 3/3 (100%) | 0.778 |
| meaning | 9 | 8/9 (89%) | 8/9 (89%) | 8/9 (89%) | 0.889 |
| picture | 5 | 1/5 (20%) | 1/5 (20%) | 3/5 (60%) | 0.247 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 3/4 (75%) | 4/4 (100%) | 4/4 (100%) | 0.833 |
| photo | 1 | 0/1 (0%) | 0/1 (0%) | 1/1 (100%) | 0.125 |
| screenshot | 15 | 11/15 (73%) | 11/15 (73%) | 12/15 (80%) | 0.741 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 15 | 13/15 (87%) | 14/15 (93%) | 14/15 (93%) | 0.889 |
| public | 7 | 3/7 (43%) | 3/7 (43%) | 5/7 (71%) | 0.462 |

Weaker-matches divider (app): median 17.5 results above it; every correct file above it for 20 of 22 queries (private 16, 14 of 15; public 19, 6 of 7). Latency: p50 285 ms, p95 496 ms (engine stage timers).

Queries without a correct file at #1 (6). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| q01 | public | meaning | Find the image of a famous fractal formed by repeatedly applying a complex quadratic recurrence | 46 | no | dense #26 |  |
| q08 | public | picture | Locate the image whose color legend identifies different roots of a polynomial | 12 | yes | bm25 #29, dense #6 |  |
| q22 | private | picture | (private) | 47 | no | dense #28 |  |
| q27 | public | picture | Find the colorful image showing regions that attract complex-number iterations to different roots | 9 | yes | bm25 #31, dense #4 |  |
| q31 | public | picture | snowy mountain peaks over autumn valley with winding road and wooden chalets | 8 | yes | clip #1 |  |
| q33 | private | inside a scan | (private) | 3 | yes | bm25 #3, dense #1 |  |

## Held-out half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 9/22 (41%) | 12/22 (54%) | 14/22 (64%) | 0.466 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 4 | 3/4 (75%) | 3/4 (75%) | 4/4 (100%) | 0.792 |
| inside a scan | 3 | 0/3 (0%) | 1/3 (33%) | 1/3 (33%) | 0.111 |
| meaning | 10 | 4/10 (40%) | 5/10 (50%) | 6/10 (60%) | 0.442 |
| picture | 5 | 2/5 (40%) | 3/5 (60%) | 3/5 (60%) | 0.467 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 3/4 (75%) | 3/4 (75%) | 4/4 (100%) | 0.792 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 17 | 5/17 (29%) | 8/17 (47%) | 9/17 (53%) | 0.358 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 16 | 6/16 (38%) | 9/16 (56%) | 11/16 (69%) | 0.453 |
| public | 6 | 3/6 (50%) | 3/6 (50%) | 3/6 (50%) | 0.500 |

Weaker-matches divider (app): median 21.5 results above it; every correct file above it for 19 of 22 queries (private 21.0, 14 of 16; public 21.5, 5 of 6). Latency: p50 266 ms, p95 455 ms (engine stage timers).

## Both halves (44 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 44 | 25/44 (57%) | 29/44 (66%) | 33/44 (75%) | 0.610 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 9 | 8/9 (89%) | 8/9 (89%) | 9/9 (100%) | 0.907 |
| inside a scan | 6 | 2/6 (33%) | 4/6 (67%) | 4/6 (67%) | 0.444 |
| meaning | 19 | 12/19 (63%) | 13/19 (68%) | 14/19 (74%) | 0.654 |
| picture | 10 | 3/10 (30%) | 4/10 (40%) | 6/10 (60%) | 0.357 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 8 | 6/8 (75%) | 7/8 (88%) | 8/8 (100%) | 0.812 |
| photo | 2 | 1/2 (50%) | 1/2 (50%) | 2/2 (100%) | 0.562 |
| screenshot | 32 | 16/32 (50%) | 19/32 (59%) | 21/32 (66%) | 0.537 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 31 | 19/31 (61%) | 23/31 (74%) | 25/31 (81%) | 0.664 |
| public | 13 | 6/13 (46%) | 6/13 (46%) | 8/13 (62%) | 0.480 |

Weaker-matches divider (app): median 19.0 results above it; every correct file above it for 39 of 44 queries (private 18, 28 of 31; public 21, 11 of 13). Latency: p50 282 ms, p95 496 ms (engine stage timers).

