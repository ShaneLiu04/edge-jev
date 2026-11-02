# AR022 parallel slot concurrency (real)

| mode | wall ms | p50 ms | req/s | speedup | quality |
| --- | ---: | ---: | ---: | ---: | --- |
| parallel_1 | 8152.0 | 2702.2 | 1.472 | 1.0x | True |
| parallel_2_auto | 8218.4 | 2698.1 | 1.46 | 0.992x | True |
| parallel_2_scheduled | 6692.9 | 2217.1 | 1.793 | 1.218x | True |
| parallel_4_auto | 4386.0 | 1444.5 | 2.736 | 1.859x | True |
| parallel_4_scheduled | 4539.4 | 1526.8 | 2.644 | 1.796x | True |

results: D:\Jev\benchmarks\results\ar022-concurrency-20261102-142142\results.json
