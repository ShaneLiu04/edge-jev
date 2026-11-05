# AR023 multi-slot persistent prefix (real cold restart)

| mode | cold wall ms | cold p50 ms | restores | warm rps | steady p50 ms | vs p1 | vs p4 auto | quality |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| parallel_1 | 10655.6 | 2155.3 | 0 | 1.127 | 2131.1 | 1.0x | 1.973x | True |
| parallel_1_mspp | 6574.4 | 2126.2 | 1 | 1.126 | 2127.4 | 1.621x | 3.197x | True |
| parallel_4_auto | 21021.5 | 1404.5 | 0 | 0.577 | 957.9 | 0.507x | 1.0x | True |
| parallel_4_scheduler | 20865.4 | 1353.8 | 0 | 0.574 | 916.2 | 0.511x | 1.007x | True |
| parallel_4_mspp | 3149.6 | 1001.3 | 4 | 0.572 | 1045.9 | 3.383x | 6.674x | True |

results: D:\Jev\benchmarks\results\ar023-mspp-20261106-013110\results.json
