# Token usage — Mlaser project

Generated 2026-09-12 by `tools/token_usage.py` from the local Claude Code transcripts (session `24376926-df7c-475a-835b-230b0f9b6974` and its workflow agents). Counts are API-billed tokens as reported in `usage`; "Total" is the sum of all input kinds plus output.

## Grand total

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 750 | 21,664 | 5,155,568 | 334,233 | 124,476,314 | 136,077 | 31,102 | 130,123,856 |

## By day

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-09 | 31 | 62 | 0 | 37,305 | 1,486,269 | 17,061 | 7,395 | 1,540,697 |
| 2026-09-11 | 461 | 13,826 | 3,192,557 | 94,887 | 82,071,486 | 71,555 | 12,149 | 85,444,311 |
| 2026-09-12 | 258 | 7,776 | 1,963,011 | 202,041 | 40,918,559 | 47,461 | 11,558 | 43,138,848 |

## By model

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| <synthetic> | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| claude-fable-5-1 | 712 | 21,592 | 5,137,423 | 296,928 | 122,188,912 | 118,997 | 23,707 | 127,763,852 |
| claude-opus-4-8 | 5 | 10 | 18,145 | 0 | 801,133 | 19 | 0 | 819,307 |
| claude-opus-5 | 31 | 62 | 0 | 37,305 | 1,486,269 | 17,061 | 7,395 | 1,540,697 |

## By source (main session vs. each workflow agent)

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| analyze:04-controller-protocol | 94 | 2,948 | 478,385 | 0 | 26,248,319 | 3,734 | 1,242 | 26,733,386 |
| analyze:05-motion-pipeline | 82 | 2,594 | 693,061 | 0 | 15,507,666 | 2,454 | 0 | 16,205,775 |
| verify:01-hardware-config | 65 | 2,020 | 311,433 | 0 | 13,711,615 | 7,101 | 0 | 14,032,169 |
| analyze:07-exe-internals | 98 | 2,896 | 616,426 | 0 | 12,442,446 | 7,419 | 0 | 13,069,187 |
| analyze:01-hardware-config | 60 | 1,860 | 561,232 | 0 | 10,686,892 | 3,899 | 0 | 11,253,883 |
| analyze:02-layer-params | 63 | 1,956 | 441,399 | 0 | 9,661,150 | 23,854 | 0 | 10,128,359 |
| analyze:09-cad-libs | 57 | 1,794 | 548,590 | 0 | 9,038,723 | 791 | 0 | 9,589,898 |
| main | 92 | 1,298 | 0 | 334,233 | 8,751,789 | 80,470 | 29,860 | 9,167,790 |
| analyze:08-runtime-logs | 60 | 1,860 | 771,518 | 0 | 7,968,849 | 1,358 | 0 | 8,743,585 |
| analyze:03-chf-format | 46 | 1,442 | 295,520 | 0 | 7,691,964 | 1,647 | 0 | 7,990,573 |
| analyze:06-ui-features | 21 | 642 | 371,310 | 0 | 2,006,369 | 2,739 | 0 | 2,381,060 |
| verify:02-layer-params | 12 | 354 | 66,694 | 0 | 760,532 | 611 | 0 | 828,191 |

## Day × source × model (raw)

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-09 · main · claude-opus-5 | 31 | 62 | 0 | 37,305 | 1,486,269 | 17,061 | 7,395 | 1,540,697 |
| 2026-09-11 · analyze:01-hardware-config · claude-fable-5-1 | 60 | 1,860 | 561,232 | 0 | 10,686,892 | 3,899 | 0 | 11,253,883 |
| 2026-09-11 · analyze:02-layer-params · claude-fable-5-1 | 63 | 1,956 | 441,399 | 0 | 9,661,150 | 23,854 | 0 | 10,128,359 |
| 2026-09-11 · analyze:03-chf-format · claude-fable-5-1 | 46 | 1,442 | 295,520 | 0 | 7,691,964 | 1,647 | 0 | 7,990,573 |
| 2026-09-11 · analyze:04-controller-protocol · claude-fable-5-1 | 94 | 2,948 | 478,385 | 0 | 26,248,319 | 3,734 | 1,242 | 26,733,386 |
| 2026-09-11 · analyze:05-motion-pipeline · claude-fable-5-1 | 82 | 2,594 | 693,061 | 0 | 15,507,666 | 2,454 | 0 | 16,205,775 |
| 2026-09-11 · analyze:06-ui-features · claude-fable-5-1 | 21 | 642 | 371,310 | 0 | 2,006,369 | 2,739 | 0 | 2,381,060 |
| 2026-09-11 · analyze:07-exe-internals · claude-fable-5-1 | 26 | 772 | 142,740 | 0 | 2,558,276 | 422 | 0 | 2,702,210 |
| 2026-09-11 · analyze:07-exe-internals · claude-opus-4-8 | 5 | 10 | 18,145 | 0 | 801,133 | 19 | 0 | 819,307 |
| 2026-09-11 · analyze:08-runtime-logs · claude-fable-5-1 | 29 | 898 | 190,765 | 0 | 3,438,424 | 598 | 0 | 3,630,685 |
| 2026-09-11 · main · <synthetic> | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026-09-11 · main · claude-fable-5-1 | 34 | 704 | 0 | 94,887 | 3,471,293 | 32,189 | 10,907 | 3,599,073 |
| 2026-09-12 · analyze:07-exe-internals · claude-fable-5-1 | 67 | 2,114 | 455,541 | 0 | 9,083,037 | 6,978 | 0 | 9,547,670 |
| 2026-09-12 · analyze:08-runtime-logs · claude-fable-5-1 | 31 | 962 | 580,753 | 0 | 4,530,425 | 760 | 0 | 5,112,900 |
| 2026-09-12 · analyze:09-cad-libs · claude-fable-5-1 | 57 | 1,794 | 548,590 | 0 | 9,038,723 | 791 | 0 | 9,589,898 |
| 2026-09-12 · main · <synthetic> | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026-09-12 · main · claude-fable-5-1 | 25 | 532 | 0 | 202,041 | 3,794,227 | 31,220 | 11,558 | 4,028,020 |
| 2026-09-12 · verify:01-hardware-config · claude-fable-5-1 | 65 | 2,020 | 311,433 | 0 | 13,711,615 | 7,101 | 0 | 14,032,169 |
| 2026-09-12 · verify:02-layer-params · claude-fable-5-1 | 12 | 354 | 66,694 | 0 | 760,532 | 611 | 0 | 828,191 |
