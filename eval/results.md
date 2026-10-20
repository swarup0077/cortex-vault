# Eval: current settings (after item z)

Run 2026-10-19T23:38:29 at commit ef51e68. Corpus: 95 files (71 public paths, 24 private). Models: BAAI/bge-small-en-v1.5, Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text, PP-OCRv6_det_small.onnx+PP-OCRv6_rec_small.onnx.

<!-- held-out runs: written by vault eval -->
## Every run on the held-out half

Each run that touched the held-out half, in the order of `eval/heldout_runs.jsonl`; the app's row. Picture: the picture queries, R@1 / R@5 / R@10. Divider: the median number of results above it, and the queries with every correct file above it.

**Held-out half**

| # | run | when, commit | files | R@1 | R@5 | R@10 | MRR@10 | picture | divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | baseline: the settings at the start of M6 item 3 | 10-19 13:10, d8744d2 | 95 | 8 | 13 | 17 | 0.464 | 2 / 3 / 3 of 5 | 17.0 | 17 of 22 |
| 2 | final: after fixes x, y, n and f; the divider's first bar (0.31) | 10-19 17:04, 6d4c860 | 95 | 7 | 13 | 16 | 0.443 | 2 / 3 / 3 of 5 | 37.0 | 21 of 22 |
| 3 | divider-rerun: the divider's bar at 0.50; ranks are the final run's, checked query by query | 10-19 17:39, 1cd19ad | 95 | as the final run | | | | | 19.0 | 18 of 22 |
| 4 | scale: the final settings, with 1,000 generated files added to the index | 10-19 18:39, b6a93f1 | 1095 | 5 | 13 | 17 | 0.395 | 1 / 4 / 4 of 5 | 9.5 | 17 of 22 |
| 5 | post-z: the visual-query rule's gap, clip_strong_gap 0.035 (item z) | 10-19 23:38, ef51e68 | 95 | 6 | 12 | 16 | 0.402 | 1 / 2 / 3 of 5 | 19.5 | 18 of 22 |
| 6 | scale: the current settings (after item z), with 1,000 generated files added to the index | 10-19 23:39, ef51e68 | 1095 | 9 | 12 | 14 | 0.466 | 2 / 3 / 3 of 5 | 21.5 | 19 of 22 |

**Both halves**

| # | run | when, commit | files | R@1 | R@5 | R@10 | MRR@10 | picture | divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | baseline: the settings at the start of M6 item 3 | 10-19 13:10, d8744d2 | 95 | 24 | 32 | 36 | 0.619 | 4 / 6 / 6 of 10 | 15.5 | 36 of 44 |
| 2 | final: after fixes x, y, n and f; the divider's first bar (0.31) | 10-19 17:04, 6d4c860 | 95 | 24 | 32 | 36 | 0.627 | 5 / 6 / 7 of 10 | 39.5 | 43 of 44 |
| 3 | divider-rerun: the divider's bar at 0.50; ranks are the final run's, checked query by query | 10-19 17:39, 1cd19ad | 95 | as the final run | | | | | 19.0 | 38 of 44 |
| 4 | scale: the final settings, with 1,000 generated files added to the index | 10-19 18:39, b6a93f1 | 1095 | 17 | 28 | 36 | 0.503 | 3 / 6 / 7 of 10 | 10.0 | 37 of 44 |
| 5 | post-z: the visual-query rule's gap, clip_strong_gap 0.035 (item z) | 10-19 23:38, ef51e68 | 95 | 23 | 30 | 35 | 0.601 | 3 / 4 / 6 of 10 | 19.5 | 38 of 44 |
| 6 | scale: the current settings (after item z), with 1,000 generated files added to the index | 10-19 23:39, ef51e68 | 1095 | 25 | 29 | 33 | 0.610 | 3 / 4 / 6 of 10 | 19.0 | 39 of 44 |

**Tuning half**

| # | run | when, commit | files | R@1 | R@5 | R@10 | MRR@10 | picture | divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | baseline: the settings at the start of M6 item 3 | 10-19 13:10, d8744d2 | 95 | 16 | 19 | 19 | 0.774 | 2 / 3 / 3 of 5 | 14.5 | 19 of 22 |
| 2 | final: after fixes x, y, n and f; the divider's first bar (0.31) | 10-19 17:04, 6d4c860 | 95 | 17 | 19 | 20 | 0.812 | 3 / 3 / 4 of 5 | 40.0 | 22 of 22 |
| 3 | divider-rerun: the divider's bar at 0.50; ranks are the final run's, checked query by query | 10-19 17:39, 1cd19ad | 95 | as the final run | | | | | 19.0 | 20 of 22 |
| 4 | scale: the final settings, with 1,000 generated files added to the index | 10-19 18:39, b6a93f1 | 1095 | 12 | 15 | 19 | 0.612 | 2 / 2 / 3 of 5 | 10.5 | 20 of 22 |
| 5 | post-z: the visual-query rule's gap, clip_strong_gap 0.035 (item z) | 10-19 23:38, ef51e68 | 95 | 17 | 18 | 19 | 0.801 | 2 / 2 / 3 of 5 | 19.5 | 20 of 22 |
| 6 | scale: the current settings (after item z), with 1,000 generated files added to the index | 10-19 23:39, ef51e68 | 1095 | 16 | 17 | 19 | 0.753 | 1 / 1 / 3 of 5 | 17.5 | 20 of 22 |

<!-- /held-out runs -->

<!-- coverage queries: written by vault eval -->
## Coverage queries (21 queries about the generated files)

Run 2026-10-20T01:20:25 at commit e33e85b, logged as `coverage`; full report in [`results_coverage.md`](results_coverage.md). Written from the generated files' contents before any search, for the §9 coverage the owner's 44 queries don't aim at: Word and PowerPoint files, kind words (filtered) and mixed queries (an exact number, code or name with words about the content). Nothing was tuned on them.

| App, overall and by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| all | 21 | 18/21 (86%) | 21/21 (100%) | 21/21 (100%) | 0.929 |
| exact word | 4 | 3/4 (75%) | 4/4 (100%) | 4/4 (100%) | 0.875 |
| filtered | 7 | 7/7 (100%) | 7/7 (100%) | 7/7 (100%) | 1.000 |
| inside a scan | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| meaning | 3 | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | 1.000 |
| mixed | 6 | 4/6 (67%) | 6/6 (100%) | 6/6 (100%) | 0.833 |

| App, by file type of the first correct file | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| doc | 4 | 4/4 (100%) | 4/4 (100%) | 4/4 (100%) | 1.000 |
| pdf | 8 | 6/8 (75%) | 8/8 (100%) | 8/8 (100%) | 0.875 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |
| slides | 6 | 5/6 (83%) | 6/6 (100%) | 6/6 (100%) | 0.917 |

| id | search type | query | correct file type | rank |
|---|---|---|---|---|
| c01 | meaning | who books the seminar room and by when | doc | 1 |
| c02 | meaning | which beach do we visit on day 3 of the Goa trip | doc | 1 |
| c03 | exact word | spice farm lunch and ferry back before dark | doc | 1 |
| c04 | exact word | singular values are the square roots of the eigenvalues of A transpose A | slides | 2 |
| c05 | exact word | office hours on Tuesday afternoon | slides | 1 |
| c06 | exact word | cross-entropy loss for classification | slides | 1 |
| c07 | meaning | compress a matrix by keeping only the largest singular values | slides | 1 |
| c08 | filtered | slides on gradient descent | slides | 1 |
| c09 | filtered | pdf about the Adam optimizer | pdf | 1 |
| c10 | filtered | screenshot of the interview schedule email | screenshot | 1 |
| c11 | filtered | photo of the moon | photo | 1 |
| c12 | filtered | deck about eigenvalues and eigenvectors | slides | 1 |
| c13 | filtered | pdf with the electricity amount due | pdf | 1 |
| c14 | filtered | docs about the project sync | doc | 1 |
| c15 | mixed | train 12345 arrival time in Madgaon | pdf | 2 |
| c16 | mixed | coach B2 berth numbers for our trip | pdf | 1 |
| c17 | mixed | Room 4B interview what to bring | screenshot | 1 |
| c18 | mixed | 212 kWh units consumed on the bill | pdf | 1 |
| c19 | mixed | Q(s a) update rule in reinforcement learning | pdf | 1 |
| c20 | mixed | HACKATHON 2026 registration deadline and prize | pdf | 2 |
| c21 | inside a scan | max pooling over a 2 x 2 window halves the feature map | pdf | 1 |

**§9 coverage, by query set** (the owner's 44 deliberately cover the owner's four search types; the rest comes from the generated files):

| queries with | owner's | generated | coverage | all | §9 asks |
|---|---|---|---|---|---|
| queries | 44 | 32 | 21 | 97 | ≥ 40 |
| exact (exact word) | 9 | 8 | 4 | 21 | ≥ 6 |
| semantic (meaning) | 19 | 10 | 3 | 32 | ≥ 6 |
| visual (picture) | 10 | 6 | 0 | 16 | ≥ 6 |
| filtered (kind words) | 0 | 0 | 7 | 7 | ≥ 6 |
| mixed | 0 | 0 | 6 | 6 | ≥ 6 |
| inside a scan (the owner's type, not in §9) | 6 | 8 | 1 | 15 | – |
| a .docx or .pptx correct file | 0 | 2 | 11 | 13 | ≥ 6 |
| a tall screenshot correct file | 0 | 5 | 3 | 8 | ≥ 3 |
| a PDF correct file with text inside an image on a page | 2 | 0 | 3 | 5 | ≥ 3 |
<!-- /coverage queries -->

## Final vs post-z

The final run 2026-10-19T17:04:07 at commit 6d4c860; this run 2026-10-19T23:38:29 at commit ef51e68.

| half | queries | n | R@1 | R@5 | R@10 | MRR@10 | above the divider (median) | every correct file above it |
|---|---|---|---|---|---|---|---|---|
| Tuning half | all | 22 | 17 → 17 (77% → 77%) | 19 → 18 (86% → 82%) | 20 → 19 (91% → 86%) | 0.812 → 0.801 | 19.0 → 19.5 | 20 → 20 of 22 |
| Tuning half | private | 15 | 12 → 13 (80% → 87%) | 14 → 14 (93% → 93%) | 14 → 14 (93% → 93%) | 0.850 → 0.900 |  |  |
| Tuning half | public | 7 | 5 → 4 (71% → 57%) | 5 → 4 (71% → 57%) | 6 → 5 (86% → 71%) | 0.730 → 0.587 |  |  |
| Held-out half | all | 22 | 7 → 6 (32% → 27%) | 13 → 12 (59% → 54%) | 16 → 16 (73% → 73%) | 0.443 → 0.402 | 19.0 → 19.5 | 18 → 18 of 22 |
| Held-out half | private | 16 | 6 → 5 (38% → 31%) | 12 → 11 (75% → 69%) | 14 → 14 (88% → 88%) | 0.539 → 0.483 |  |  |
| Held-out half | public | 6 | 1 → 1 (17% → 17%) | 1 → 1 (17% → 17%) | 2 → 2 (33% → 33%) | 0.185 → 0.185 |  |  |
| Both halves | all | 44 | 24 → 23 (54% → 52%) | 32 → 30 (73% → 68%) | 36 → 35 (82% → 80%) | 0.627 → 0.601 | 19.0 → 19.5 | 38 → 38 of 44 |
| Both halves | private | 31 | 18 → 18 (58% → 58%) | 26 → 25 (84% → 81%) | 28 → 28 (90% → 90%) | 0.690 → 0.685 |  |  |
| Both halves | public | 13 | 6 → 5 (46% → 38%) | 6 → 5 (46% → 38%) | 8 → 7 (62% → 54%) | 0.479 → 0.402 |  |  |

## Tuning half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 17/22 (77%) | 18/22 (82%) | 19/22 (86%) | 0.801 |
| 1. BM25 only | 22 | 14/22 (64%) | 15/22 (68%) | 16/22 (73%) | 0.657 |
| 2. Dense only | 22 | 13/22 (59%) | 16/22 (73%) | 19/22 (86%) | 0.654 |
| 3. CLIP only | 22 | 7/22 (32%) | 13/22 (59%) | 15/22 (68%) | 0.444 |
| 4. BM25 + dense | 22 | 14/22 (64%) | 16/22 (73%) | 19/22 (86%) | 0.688 |
| 5. All three, CLIP ungated | 22 | 14/22 (64%) | 16/22 (73%) | 18/22 (82%) | 0.686 |
| 7. All three, gated, no every-word list | 22 | 17/22 (77%) | 18/22 (82%) | 19/22 (86%) | 0.801 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 5 | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) | 1.000 |
| inside a scan | 3 | 2/3 (67%) | 3/3 (100%) | 3/3 (100%) | 0.833 |
| meaning | 9 | 8/9 (89%) | 8/9 (89%) | 8/9 (89%) | 0.889 |
| picture | 5 | 2/5 (40%) | 2/5 (40%) | 3/5 (60%) | 0.422 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 3/4 (75%) | 4/4 (100%) | 4/4 (100%) | 0.875 |
| photo | 1 | 0/1 (0%) | 0/1 (0%) | 0/1 (0%) | 0.000 |
| screenshot | 15 | 12/15 (80%) | 12/15 (80%) | 13/15 (87%) | 0.807 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 15 | 13/15 (87%) | 14/15 (93%) | 14/15 (93%) | 0.900 |
| public | 7 | 4/7 (57%) | 4/7 (57%) | 5/7 (71%) | 0.587 |

Weaker-matches divider (app): median 19.5 results above it; every correct file above it for 20 of 22 queries (private 19, 14 of 15; public 28, 6 of 7). Latency: p50 218 ms, p95 379 ms (engine stage timers).

Queries without a correct file at #1 (5). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| q01 | public | meaning | Find the image of a famous fractal formed by repeatedly applying a complex quadratic recurrence | 43 | no | dense #24 | bm25 –, dense 24, clip 9 |
| q08 | public | picture | Locate the image whose color legend identifies different roots of a polynomial | 9 | yes | bm25 #29, dense #6 | bm25 29, dense 6, clip 8 |
| q22 | private | picture | (private) | 46 | no | dense #26 | bm25 –, dense 26, clip 5 |
| q31 | public | picture | snowy mountain peaks over autumn valley with winding road and wooden chalets | 11 | yes | clip #1 | bm25 –, dense –, clip 1 |
| q33 | private | inside a scan | (private) | 2 | yes | bm25 #3, dense #1 | bm25 3, dense 1, clip – |

## Held-out half (22 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 22 | 6/22 (27%) | 12/22 (54%) | 16/22 (73%) | 0.402 |
| 1. BM25 only | 22 | 7/22 (32%) | 9/22 (41%) | 9/22 (41%) | 0.364 |
| 2. Dense only | 22 | 5/22 (23%) | 11/22 (50%) | 15/22 (68%) | 0.330 |
| 3. CLIP only | 22 | 2/22 (9%) | 9/22 (41%) | 14/22 (64%) | 0.229 |
| 4. BM25 + dense | 22 | 7/22 (32%) | 9/22 (41%) | 12/22 (54%) | 0.373 |
| 5. All three, CLIP ungated | 22 | 5/22 (23%) | 14/22 (64%) | 16/22 (73%) | 0.373 |
| 7. All three, gated, no every-word list | 22 | 6/22 (27%) | 12/22 (54%) | 16/22 (73%) | 0.402 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 4 | 2/4 (50%) | 4/4 (100%) | 4/4 (100%) | 0.675 |
| inside a scan | 3 | 1/3 (33%) | 1/3 (33%) | 2/3 (67%) | 0.375 |
| meaning | 10 | 2/10 (20%) | 5/10 (50%) | 7/10 (70%) | 0.341 |
| picture | 5 | 1/5 (20%) | 2/5 (40%) | 3/5 (60%) | 0.320 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 4 | 3/4 (75%) | 4/4 (100%) | 4/4 (100%) | 0.800 |
| photo | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1.000 |
| screenshot | 17 | 2/17 (12%) | 7/17 (41%) | 11/17 (65%) | 0.273 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 16 | 5/16 (31%) | 11/16 (69%) | 14/16 (88%) | 0.483 |
| public | 6 | 1/6 (17%) | 1/6 (17%) | 2/6 (33%) | 0.185 |

Weaker-matches divider (app): median 19.5 results above it; every correct file above it for 18 of 22 queries (private 19.5, 14 of 16; public 20.0, 4 of 6). Latency: p50 218 ms, p95 399 ms (engine stage timers).

Queries without a correct file at #1 (16). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| q02 | private | meaning | (private) | 2 | no | bm25 #1, dense #8 | bm25 1, dense 8, clip – |
| q04 | private | picture | (private) | 10 | yes | dense #19, clip #1 | bm25 –, dense 19, clip 1 |
| q05 | private | meaning | (private) | 11 | yes | bm25 #21, dense #7 | bm25 21, dense 7, clip 7 |
| q06 | public | picture | Search for the picture with a dark central shape surrounded by intricate purple branching boundaries | not in 50 | no | – | bm25 –, dense –, clip – |
| q07 | private | meaning | (private) | 2 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip 8 |
| q09 | private | meaning | (private) | 18 | yes | bm25 #15, dense #30 | bm25 15, dense 30, clip 9 |
| q10 | private | exact word | (private) | 2 | no | bm25 #1, dense #3 | bm25 1, dense 3, clip 5 |
| q15 | public | picture | Which image has horizontal and vertical axes labeled with real and imaginary components? | 14 | yes | bm25 #32, dense #9 | bm25 31, dense 9, clip 4 |
| q16 | private | picture | (private) | 2 | yes | bm25 #25, dense #5, clip #2 | bm25 25, dense 5, clip 2 |
| q18 | private | exact word | (private) | 5 | yes | dense #12, clip #2 | bm25 –, dense 12, clip 2 |
| q19 | public | meaning | Find the plot showing regions of convergence for an iterative method applied to a cubic polynomial | 21 | no | dense #8 | bm25 –, dense 8, clip 9 |
| q20 | private | inside a scan | (private) | 8 | yes | dense #15, clip #2 | bm25 –, dense 15, clip 2 |
| q21 | public | meaning | Search for the plot with a complex function involving a negative power of z | 9 | yes | bm25 #11, dense #5 | bm25 11, dense 5, clip 10 |
| q24 | public | inside a scan | Search for the image where the function label contains z raised to the third power | 12 | yes | bm25 #36, dense #5 | bm25 36, dense 5, clip 18 |
| q25 | private | meaning | (private) | 10 | yes | dense #16, clip #4 | bm25 –, dense 16, clip 4 |
| q37 | private | meaning | (private) | 5 | yes | bm25 #2, dense #3 | bm25 2, dense 3, clip – |

## Both halves (44 queries)

| Setting | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| 6. All three, CLIP gated (the app) | 44 | 23/44 (52%) | 30/44 (68%) | 35/44 (80%) | 0.601 |
| 1. BM25 only | 44 | 21/44 (48%) | 24/44 (54%) | 25/44 (57%) | 0.510 |
| 2. Dense only | 44 | 18/44 (41%) | 27/44 (61%) | 34/44 (77%) | 0.492 |
| 3. CLIP only | 44 | 9/44 (20%) | 22/44 (50%) | 29/44 (66%) | 0.337 |
| 4. BM25 + dense | 44 | 21/44 (48%) | 25/44 (57%) | 31/44 (70%) | 0.530 |
| 5. All three, CLIP ungated | 44 | 19/44 (43%) | 30/44 (68%) | 34/44 (77%) | 0.530 |
| 7. All three, gated, no every-word list | 44 | 23/44 (52%) | 30/44 (68%) | 35/44 (80%) | 0.601 |

| App, by search type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| exact word | 9 | 7/9 (78%) | 9/9 (100%) | 9/9 (100%) | 0.856 |
| inside a scan | 6 | 3/6 (50%) | 4/6 (67%) | 5/6 (83%) | 0.604 |
| meaning | 19 | 10/19 (53%) | 13/19 (68%) | 15/19 (79%) | 0.601 |
| picture | 10 | 3/10 (30%) | 4/10 (40%) | 6/10 (60%) | 0.371 |

| App, by file type | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| pdf | 8 | 6/8 (75%) | 8/8 (100%) | 8/8 (100%) | 0.838 |
| photo | 2 | 1/2 (50%) | 1/2 (50%) | 1/2 (50%) | 0.500 |
| screenshot | 32 | 14/32 (44%) | 19/32 (59%) | 24/32 (75%) | 0.523 |
| text | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.000 |

| App, by set | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| private | 31 | 18/31 (58%) | 25/31 (81%) | 28/31 (90%) | 0.685 |
| public | 13 | 5/13 (38%) | 5/13 (38%) | 7/13 (54%) | 0.402 |

Weaker-matches divider (app): median 19.5 results above it; every correct file above it for 38 of 44 queries (private 19, 28 of 31; public 21, 10 of 13). Latency: p50 221 ms, p95 399 ms (engine stage timers).

Queries without a correct file at #1 (21). Found by: each method's rank for the first correct file in the app (for a weaker match, in the method's full list); single-method rank: its rank with that method alone.

| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |
|---|---|---|---|---|---|---|---|
| q01 | public | meaning | Find the image of a famous fractal formed by repeatedly applying a complex quadratic recurrence | 43 | no | dense #24 | bm25 –, dense 24, clip 9 |
| q02 | private | meaning | (private) | 2 | no | bm25 #1, dense #8 | bm25 1, dense 8, clip – |
| q04 | private | picture | (private) | 10 | yes | dense #19, clip #1 | bm25 –, dense 19, clip 1 |
| q05 | private | meaning | (private) | 11 | yes | bm25 #21, dense #7 | bm25 21, dense 7, clip 7 |
| q06 | public | picture | Search for the picture with a dark central shape surrounded by intricate purple branching boundaries | not in 50 | no | – | bm25 –, dense –, clip – |
| q07 | private | meaning | (private) | 2 | yes | bm25 #1, dense #1 | bm25 1, dense 1, clip 8 |
| q08 | public | picture | Locate the image whose color legend identifies different roots of a polynomial | 9 | yes | bm25 #29, dense #6 | bm25 29, dense 6, clip 8 |
| q09 | private | meaning | (private) | 18 | yes | bm25 #15, dense #30 | bm25 15, dense 30, clip 9 |
| q10 | private | exact word | (private) | 2 | no | bm25 #1, dense #3 | bm25 1, dense 3, clip 5 |
| q15 | public | picture | Which image has horizontal and vertical axes labeled with real and imaginary components? | 14 | yes | bm25 #32, dense #9 | bm25 31, dense 9, clip 4 |
| q16 | private | picture | (private) | 2 | yes | bm25 #25, dense #5, clip #2 | bm25 25, dense 5, clip 2 |
| q18 | private | exact word | (private) | 5 | yes | dense #12, clip #2 | bm25 –, dense 12, clip 2 |
| q19 | public | meaning | Find the plot showing regions of convergence for an iterative method applied to a cubic polynomial | 21 | no | dense #8 | bm25 –, dense 8, clip 9 |
| q20 | private | inside a scan | (private) | 8 | yes | dense #15, clip #2 | bm25 –, dense 15, clip 2 |
| q21 | public | meaning | Search for the plot with a complex function involving a negative power of z | 9 | yes | bm25 #11, dense #5 | bm25 11, dense 5, clip 10 |
| q22 | private | picture | (private) | 46 | no | dense #26 | bm25 –, dense 26, clip 5 |
| q24 | public | inside a scan | Search for the image where the function label contains z raised to the third power | 12 | yes | bm25 #36, dense #5 | bm25 36, dense 5, clip 18 |
| q25 | private | meaning | (private) | 10 | yes | dense #16, clip #4 | bm25 –, dense 16, clip 4 |
| q31 | public | picture | snowy mountain peaks over autumn valley with winding road and wooden chalets | 11 | yes | clip #1 | bm25 –, dense –, clip 1 |
| q33 | private | inside a scan | (private) | 2 | yes | bm25 #3, dense #1 | bm25 3, dense 1, clip – |
| q37 | private | meaning | (private) | 5 | yes | bm25 #2, dense #3 | bm25 2, dense 3, clip – |

