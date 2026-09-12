# Token usage — Mlaser project

Generated 2026-09-12 by `tools/token_usage.py` from the local Claude Code transcripts (session `24376926-df7c-475a-835b-230b0f9b6974` and its workflow agents). Counts are API-billed tokens as reported in `usage`; "Total" is the sum of all input kinds plus output.

## Grand total

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 1,194 | 35,404 | 9,624,754 | 551,721 | 191,112,858 | 1,879,518 | 778,322 | 203,204,255 |

## By day

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-09 | 31 | 62 | 0 | 37,305 | 1,486,269 | 17,061 | 7,395 | 1,540,697 |
| 2026-09-11 | 461 | 13,826 | 3,192,557 | 94,887 | 82,061,967 | 661,018 | 282,497 | 86,024,255 |
| 2026-09-12 | 702 | 21,516 | 6,432,197 | 419,529 | 107,564,622 | 1,201,439 | 488,430 | 115,639,303 |

## By model

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| <synthetic> | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| claude-fable-5-1 | 1,155 | 35,300 | 9,600,987 | 514,416 | 188,674,685 | 1,857,398 | 768,569 | 200,682,786 |
| claude-opus-4-8 | 6 | 42 | 23,767 | 0 | 951,904 | 5,059 | 2,358 | 980,772 |
| claude-opus-5 | 31 | 62 | 0 | 37,305 | 1,486,269 | 17,061 | 7,395 | 1,540,697 |

## By source (main session vs. each workflow agent)

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| analyze:04-controller-protocol | 94 | 2,948 | 478,385 | 0 | 26,248,319 | 138,343 | 74,502 | 26,867,995 |
| analyze:05-motion-pipeline | 82 | 2,594 | 693,061 | 0 | 15,507,666 | 126,595 | 60,051 | 16,329,916 |
| verify:09-cad-libs | 99 | 3,138 | 343,321 | 0 | 15,206,422 | 114,109 | 49,784 | 15,666,990 |
| verify:01-hardware-config | 67 | 2,056 | 318,835 | 0 | 14,341,989 | 68,612 | 31,149 | 14,731,492 |
| analyze:07-exe-internals | 98 | 2,896 | 616,426 | 0 | 12,432,927 | 144,507 | 48,829 | 13,196,756 |
| analyze:01-hardware-config | 60 | 1,860 | 561,232 | 0 | 10,686,892 | 91,081 | 25,748 | 11,341,065 |
| verify:07-exe-internals | 62 | 1,896 | 551,521 | 0 | 10,136,126 | 97,921 | 34,304 | 10,787,464 |
| analyze:02-layer-params | 63 | 1,956 | 441,399 | 0 | 9,661,150 | 99,142 | 34,176 | 10,203,647 |
| main | 96 | 1,398 | 0 | 551,721 | 9,459,084 | 84,343 | 30,995 | 10,096,546 |
| analyze:09-cad-libs | 57 | 1,794 | 548,590 | 0 | 9,038,723 | 86,957 | 25,543 | 9,676,064 |
| verify:03-chf-format | 62 | 1,954 | 201,244 | 0 | 9,202,431 | 74,682 | 37,986 | 9,480,311 |
| verify:04-controller-protocol | 64 | 1,990 | 410,385 | 0 | 8,782,290 | 91,826 | 43,123 | 9,286,491 |
| analyze:08-runtime-logs | 60 | 1,860 | 771,518 | 0 | 7,968,849 | 129,677 | 57,131 | 8,871,904 |
| analyze:03-chf-format | 46 | 1,442 | 295,520 | 0 | 7,691,964 | 89,333 | 37,427 | 8,078,259 |
| synthesize | 18 | 518 | 1,615,747 | 0 | 4,687,486 | 67,426 | 23,868 | 6,371,177 |
| verify:05-motion-pipeline | 42 | 1,314 | 430,063 | 0 | 5,149,364 | 104,165 | 57,590 | 5,684,906 |
| verify:06-ui-features | 36 | 1,122 | 163,538 | 0 | 4,341,962 | 50,865 | 16,678 | 4,557,487 |
| verify:08-runtime-logs | 24 | 738 | 212,256 | 0 | 3,399,515 | 80,909 | 35,968 | 3,693,418 |
| verify:02-layer-params | 25 | 742 | 211,564 | 0 | 2,871,134 | 69,048 | 26,745 | 3,152,488 |
| critique | 18 | 546 | 388,839 | 0 | 2,292,196 | 57,770 | 23,337 | 2,739,351 |
| analyze:06-ui-features | 21 | 642 | 371,310 | 0 | 2,006,369 | 12,207 | 3,388 | 2,390,528 |

## Day × source × model (raw)

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-09 · main · claude-opus-5 | 31 | 62 | 0 | 37,305 | 1,486,269 | 17,061 | 7,395 | 1,540,697 |
| 2026-09-11 · analyze:01-hardware-config · claude-fable-5-1 | 60 | 1,860 | 561,232 | 0 | 10,686,892 | 91,081 | 25,748 | 11,341,065 |
| 2026-09-11 · analyze:02-layer-params · claude-fable-5-1 | 63 | 1,956 | 441,399 | 0 | 9,661,150 | 99,142 | 34,176 | 10,203,647 |
| 2026-09-11 · analyze:03-chf-format · claude-fable-5-1 | 46 | 1,442 | 295,520 | 0 | 7,691,964 | 89,333 | 37,427 | 8,078,259 |
| 2026-09-11 · analyze:04-controller-protocol · claude-fable-5-1 | 94 | 2,948 | 478,385 | 0 | 26,248,319 | 138,343 | 74,502 | 26,867,995 |
| 2026-09-11 · analyze:05-motion-pipeline · claude-fable-5-1 | 82 | 2,594 | 693,061 | 0 | 15,507,666 | 126,595 | 60,051 | 16,329,916 |
| 2026-09-11 · analyze:06-ui-features · claude-fable-5-1 | 21 | 642 | 371,310 | 0 | 2,006,369 | 12,207 | 3,388 | 2,390,528 |
| 2026-09-11 · analyze:07-exe-internals · claude-fable-5-1 | 25 | 740 | 137,118 | 0 | 2,397,986 | 30,440 | 14,501 | 2,566,284 |
| 2026-09-11 · analyze:07-exe-internals · claude-opus-4-8 | 6 | 42 | 23,767 | 0 | 951,904 | 5,059 | 2,358 | 980,772 |
| 2026-09-11 · analyze:08-runtime-logs · claude-fable-5-1 | 29 | 898 | 190,765 | 0 | 3,438,424 | 36,629 | 19,439 | 3,666,716 |
| 2026-09-11 · main · <synthetic> | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026-09-11 · main · claude-fable-5-1 | 34 | 704 | 0 | 94,887 | 3,471,293 | 32,189 | 10,907 | 3,599,073 |
| 2026-09-12 · analyze:07-exe-internals · claude-fable-5-1 | 67 | 2,114 | 455,541 | 0 | 9,083,037 | 109,008 | 31,970 | 9,649,700 |
| 2026-09-12 · analyze:08-runtime-logs · claude-fable-5-1 | 31 | 962 | 580,753 | 0 | 4,530,425 | 93,048 | 37,692 | 5,205,188 |
| 2026-09-12 · analyze:09-cad-libs · claude-fable-5-1 | 57 | 1,794 | 548,590 | 0 | 9,038,723 | 86,957 | 25,543 | 9,676,064 |
| 2026-09-12 · critique · claude-fable-5-1 | 18 | 546 | 388,839 | 0 | 2,292,196 | 57,770 | 23,337 | 2,739,351 |
| 2026-09-12 · main · <synthetic> | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026-09-12 · main · claude-fable-5-1 | 29 | 632 | 0 | 419,529 | 4,501,522 | 35,093 | 12,693 | 4,956,776 |
| 2026-09-12 · synthesize · claude-fable-5-1 | 18 | 518 | 1,615,747 | 0 | 4,687,486 | 67,426 | 23,868 | 6,371,177 |
| 2026-09-12 · verify:01-hardware-config · claude-fable-5-1 | 67 | 2,056 | 318,835 | 0 | 14,341,989 | 68,612 | 31,149 | 14,731,492 |
| 2026-09-12 · verify:02-layer-params · claude-fable-5-1 | 25 | 742 | 211,564 | 0 | 2,871,134 | 69,048 | 26,745 | 3,152,488 |
| 2026-09-12 · verify:03-chf-format · claude-fable-5-1 | 62 | 1,954 | 201,244 | 0 | 9,202,431 | 74,682 | 37,986 | 9,480,311 |
| 2026-09-12 · verify:04-controller-protocol · claude-fable-5-1 | 64 | 1,990 | 410,385 | 0 | 8,782,290 | 91,826 | 43,123 | 9,286,491 |
| 2026-09-12 · verify:05-motion-pipeline · claude-fable-5-1 | 42 | 1,314 | 430,063 | 0 | 5,149,364 | 104,165 | 57,590 | 5,684,906 |
| 2026-09-12 · verify:06-ui-features · claude-fable-5-1 | 36 | 1,122 | 163,538 | 0 | 4,341,962 | 50,865 | 16,678 | 4,557,487 |
| 2026-09-12 · verify:07-exe-internals · claude-fable-5-1 | 62 | 1,896 | 551,521 | 0 | 10,136,126 | 97,921 | 34,304 | 10,787,464 |
| 2026-09-12 · verify:08-runtime-logs · claude-fable-5-1 | 24 | 738 | 212,256 | 0 | 3,399,515 | 80,909 | 35,968 | 3,693,418 |
| 2026-09-12 · verify:09-cad-libs · claude-fable-5-1 | 99 | 3,138 | 343,321 | 0 | 15,206,422 | 114,109 | 49,784 | 15,666,990 |
