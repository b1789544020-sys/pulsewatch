#!/usr/bin/env python3
"""采集器自测：跑 2.2 秒后打印一帧真实数据，用来验证 /proc 读取是否正确"""
import time
from collector import MetricsCollector

c = MetricsCollector()
c.start()
time.sleep(2.2)
s = c.snapshot

print("=== PulseWatch 采集器自测 ===")
print("主机:", s["hostname"], "|", s["os"], "| 内核", s["kernel"])
print("CPU%:", s["cpu_percent"], "| 检测到核心数:", len(s["per_cpu"]))
print("内存%:", s["mem"]["percent"], "| Swap%:", s["swap"]["percent"],
      "| 磁盘%:", s["disk"]["percent"])
print("网络: 接收", s["net"]["rx_rate"], "B/s  发送", s["net"]["tx_rate"], "B/s")
print("负载(1/5/15分钟):", s["loadavg"], "| 运行秒数:", s["uptime_s"])
print("Top 5 进程:")
for p in s["procs"][:5]:
    print(f"  PID {p['pid']:>7}  CPU {p['cpu']:>5.1f}%  MEM {p['mem_mb']:>8.1f}MB  {p['name']}")

assert s["cpu_count"] >= 1
assert 0 <= s["cpu_percent"] <= 100
assert 0 <= s["mem"]["percent"] <= 100
assert s["mem"]["total"] > 0
print("\n✅ 全部断言通过，采集器工作正常")
