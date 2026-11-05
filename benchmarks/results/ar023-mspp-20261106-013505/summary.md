# AR023 multi-slot persistent prefix (real cold restart)

| mode | cold wall ms | cold p50 ms | restores | warm rps | steady p50 ms | vs p1 | vs p4 auto | quality |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| parallel_1 | 10719.5 | 2185.6 | 0 | 1.118 | 2151.8 | 1.0x | 1.955x | True |
| parallel_1_mspp | 6612.7 | 2157.0 | 1 | 1.121 | 2109.8 | 1.621x | 3.17x | True |
| parallel_4_auto | 20959.0 | 1381.6 | 0 | 0.573 | 949.1 | 0.511x | 1.0x | True |
| parallel_4_scheduler | 20236.0 | 1192.1 | 0 | 0.577 | 914.8 | 0.53x | 1.036x | True |
| parallel_4_mspp | 3123.4 | 1056.6 | 4 | 0.566 | 896.0 | 3.432x | 6.71x | True |

results: D:\Jev\benchmarks\results\ar023-mspp-20261106-013505\results.json
