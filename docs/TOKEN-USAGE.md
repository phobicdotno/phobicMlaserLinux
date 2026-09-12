# Token usage — Mlaser project

Generated 2026-09-12 by `tools/token_usage.py` from the local Claude Code transcripts (session `24376926-df7c-475a-835b-230b0f9b6974` and its workflow agents). Counts are API-billed tokens as reported in `usage`; "Total" is the sum of all input kinds plus output.

## Grand total

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 756 | 21,800 | 5,179,094 | 337,670 | 125,525,342 | 1,072,467 | 432,030 | 132,136,373 |

## By day

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-09 | 31 | 62 | 0 | 37,305 | 1,486,269 | 17,061 | 7,395 | 1,540,697 |
| 2026-09-11 | 461 | 13,826 | 3,192,557 | 94,887 | 82,061,967 | 661,018 | 282,497 | 86,024,255 |
| 2026-09-12 | 264 | 7,912 | 1,986,537 | 205,478 | 41,977,106 | 394,388 | 142,138 | 44,571,421 |

## By model

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| <synthetic> | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| claude-fable-5-1 | 717 | 21,696 | 5,155,327 | 300,365 | 123,087,169 | 1,050,347 | 422,277 | 129,614,904 |
| claude-opus-4-8 | 6 | 42 | 23,767 | 0 | 951,904 | 5,059 | 2,358 | 980,772 |
| claude-opus-5 | 31 | 62 | 0 | 37,305 | 1,486,269 | 17,061 | 7,395 | 1,540,697 |

## By source (main session vs. each workflow agent)

| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| analyze:04-controller-protocol | 94 | 2,948 | 478,385 | 0 | 26,248,319 | 138,343 | 74,502 | 26,867,995 |
| analyze:05-motion-pipeline | 82 | 2,594 | 693,061 | 0 | 15,507,666 | 126,595 | 60,051 | 16,329,916 |
| verify:01-hardware-config | 66 | 2,024 | 314,627 | 0 | 14,025,205 | 61,965 | 31,044 | 14,403,821 |
| analyze:07-exe-internals | 98 | 2,896 | 616,426 | 0 | 12,432,927 | 144,507 | 48,829 | 13,196,756 |
| analyze:01-hardware-config | 60 | 1,860 | 561,232 | 0 | 10,686,892 | 91,081 | 25,748 | 11,341,065 |
| analyze:02-layer-params | 63 | 1,956 | 441,399 | 0 | 9,661,150 | 99,142 | 34,176 | 10,203,647 |
| analyze:09-cad-libs | 57 | 1,794 | 548,590 | 0 | 9,038,723 | 86,957 | 25,543 | 9,676,064 |
| main | 94 | 1,362 | 0 | 337,670 | 9,203,765 | 82,731 | 30,595 | 9,625,528 |
| analyze:08-runtime-logs | 60 | 1,860 | 771,518 | 0 | 7,968,849 | 129,677 | 57,131 | 8,871,904 |
| analyze:03-chf-format | 46 | 1,442 | 295,520 | 0 | 7,691,964 | 89,333 | 37,427 | 8,078,259 |
| analyze:06-ui-features | 21 | 642 | 371,310 | 0 | 2,006,369 | 12,207 | 3,388 | 2,390,528 |
| verify:02-layer-params | 15 | 422 | 87,026 | 0 | 1,053,513 | 9,929 | 3,596 | 1,150,890 |

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
| 2026-09-12 · main · <synthetic> | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026-09-12 · main · claude-fable-5-1 | 27 | 596 | 0 | 205,478 | 4,246,203 | 33,481 | 12,293 | 4,485,758 |
| 2026-09-12 · verify:01-hardware-config · claude-fable-5-1 | 66 | 2,024 | 314,627 | 0 | 14,025,205 | 61,965 | 31,044 | 14,403,821 |
| 2026-09-12 · verify:02-layer-params · claude-fable-5-1 | 15 | 422 | 87,026 | 0 | 1,053,513 | 9,929 | 3,596 | 1,150,890 |
