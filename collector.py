#!/usr/bin/env python3
"""
PulseWatch 采集器
=================
直接读取 Linux 内核的 /proc 台账，采集：
  CPU 总占用 / 每个核心占用 / 内存 / 交换分区 / 磁盘 /
  网络收发速率 / 最耗 CPU 的进程 / 负载 / 运行时间

设计要点：
  CPU 占用率和网卡速率都不是"读一个数"就能得到的，
  必须间隔采样两次，算差值，所以本模块在后台线程里
  以固定间隔持续采样，外部随时取最新快照即可。

零第三方依赖，只用标准库。
"""

import os
import time
import threading

# 内核统计信息里的单位换算
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")          # 一个内存页多少字节
CLK_TCK = os.sysconf("SC_CLK_TCK")              # 每秒多少个 CPU 时钟滴答


# ---------------------------------------------------------------- 原始读取
def _read_cpu_total():
    """读 /proc/stat 第一行，返回 (总滴答数, 空闲滴答数)"""
    with open("/proc/stat") as f:
        vals = list(map(int, f.readline().split()[1:]))
    # 字段: user nice system idle iowait irq softirq steal ...
    idle = vals[3] + vals[4]
    return sum(vals), idle


def _read_per_cpu():
    """每个核心各自的 (总滴答, 空闲滴答)"""
    result = []
    with open("/proc/stat") as f:
        for line in f:
            # cpu0, cpu1 ... 才是每核数据，聚合行是 "cpu "
            if len(line) > 3 and line.startswith("cpu") and line[3].isdigit():
                vals = list(map(int, line.split()[1:]))
                result.append((sum(vals), vals[3] + vals[4]))
    return result


def _read_mem():
    """读 /proc/meminfo，返回字节数"""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, rest = line.split(":", 1)
            info[key] = int(rest.split()[0]) * 1024  # 文件里单位是 KB
    return info


def _read_net():
    """汇总所有真实网卡（排除 lo 回环）的累计收/发字节数"""
    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in f:
            if ":" not in line:
                continue
            name, data = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = data.split()
            rx += int(fields[0])   # 接收字节
            tx += int(fields[8])   # 发送字节
    return rx, tx


def _read_procs():
    """
    遍历 /proc/<pid>，返回 {pid: (CPU滴答累计, 进程名, 常驻内存字节)}
    /proc/<pid>/stat 里进程名带括号且可能含空格/括号，
    所以用最后一个 ')' 的位置来切分，这是标准稳妥做法。
    """
    procs = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/stat") as f:
                raw = f.read()
            # 名字夹在第一个 '(' 和最后一个 ')' 之间
            # （PID 是多位数字，且名字本身可能含空格甚至括号，不能按位置切）
            lpar = raw.find("(")
            cut = raw.rfind(")")
            name = raw[lpar + 1:cut]
            f_vals = raw[cut + 2:].split()
            # 切分后 f_vals[0] 对应第 3 字段 state
            utime = int(f_vals[11])     # 第 14 字段：用户态滴答
            stime = int(f_vals[12])     # 第 15 字段：内核态滴答
            rss_pages = int(f_vals[21]) # 第 24 字段：常驻内存页数
            procs[pid] = (utime + stime, name, rss_pages * PAGE_SIZE)
        except (FileNotFoundError, ProcessLookupError, PermissionError,
                IndexError, ValueError):
            # 进程可能刚好退出、或内核线程不允许读取 —— 跳过即可
            continue
    return procs


def _distro_name():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "Linux"


# ---------------------------------------------------------------- 采集器
class MetricsCollector:
    """后台持续采样，最新结果存于 self.snapshot"""

    def __init__(self, interval=1.0):
        self.interval = interval
        self._thread = None
        self._running = False
        self.snapshot = None

        # 记录上一次采样值，供下次做差
        self._prev_cpu = _read_cpu_total()
        self._prev_per_cpu = _read_per_cpu()
        self._prev_net = _read_net()
        self._prev_procs = _read_procs()
        self._prev_time = time.time()

    def _sample_once(self):
        now = time.time()
        dt = max(now - self._prev_time, 0.001)

        # ---- CPU 总占用 ----
        cpu_total, cpu_idle = _read_cpu_total()
        p_total, p_idle = self._prev_cpu
        total_d = cpu_total - p_total
        idle_d = cpu_idle - p_idle
        cpu_percent = 100.0 * (1.0 - idle_d / total_d) if total_d else 0.0
        cpu_percent = max(0.0, min(100.0, cpu_percent))

        # ---- 每个核心 ----
        per_cpu = []
        for (t, i), (pt, pi) in zip(_read_per_cpu(), self._prev_per_cpu):
            td, id_ = t - pt, i - pi
            pct = 100.0 * (1.0 - id_ / td) if td else 0.0
            per_cpu.append(round(max(0.0, min(100.0, pct)), 1))

        # ---- 内存 ----
        mem = _read_mem()
        mem_total = mem["MemTotal"]
        mem_avail = mem.get("MemAvailable", mem["MemFree"])
        mem_used = mem_total - mem_avail
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = swap_total - swap_free

        # ---- 磁盘（根分区）----
        st = os.statvfs("/")
        disk_total = st.f_blocks * st.f_frsize
        disk_free = st.f_bavail * st.f_frsize
        disk_used = disk_total - disk_free

        # ---- 网络速率（字节/秒）----
        rx, tx = _read_net()
        rx_rate = (rx - self._prev_net[0]) / dt
        tx_rate = (tx - self._prev_net[1]) / dt

        # ---- Top 进程：按两次采样间消耗的 CPU 滴答排序 ----
        procs_now = _read_procs()
        proc_list = []
        for pid, (ticks, name, rss) in procs_now.items():
            if pid not in self._prev_procs:
                continue
            prev_ticks = self._prev_procs[pid][0]
            tick_d = ticks - prev_ticks
            if tick_d < 0:
                continue
            # 占整机 CPU 容量的百分比（12 核机器上总和可超过 100）
            cpu_pct = 100.0 * tick_d / total_d if total_d else 0.0
            proc_list.append({
                "pid": pid,
                "name": name,
                "cpu": round(cpu_pct, 1),
                "mem_mb": round(rss / 1024 / 1024, 1),
            })
        proc_list.sort(key=lambda x: x["cpu"], reverse=True)

        # ---- 运行时间 / 负载 ----
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        load1, load5, load15 = os.getloadavg()

        self.snapshot = {
            "ts": round(now, 2),
            "hostname": os.uname().nodename,
            "os": _distro_name(),
            "kernel": os.uname().release,
            "cpu_count": os.cpu_count(),
            "uptime_s": round(uptime),
            "loadavg": [round(load1, 2), round(load5, 2), round(load15, 2)],
            "cpu_percent": round(cpu_percent, 1),
            "per_cpu": per_cpu,
            "mem": {
                "total": mem_total,
                "used": mem_used,
                "available": mem_avail,
                "percent": round(100.0 * mem_used / mem_total, 1),
            },
            "swap": {
                "total": swap_total,
                "used": swap_used,
                "percent": round(100.0 * swap_used / swap_total, 1)
                if swap_total else 0.0,
            },
            "disk": {
                "total": disk_total,
                "used": disk_used,
                "free": disk_free,
                "percent": round(100.0 * disk_used / disk_total, 1),
            },
            "net": {
                "rx_rate": max(0, round(rx_rate)),
                "tx_rate": max(0, round(tx_rate)),
                "rx_total": rx,
                "tx_total": tx,
            },
            "procs": proc_list[:8],
        }

        # 更新"上一次"
        self._prev_cpu = (cpu_total, cpu_idle)
        self._prev_per_cpu = _read_per_cpu()
        self._prev_net = (rx, tx)
        self._prev_procs = procs_now
        self._prev_time = now

    def _loop(self):
        while self._running:
            try:
                self._sample_once()
            except Exception as exc:  # 采集出错不能让后台线程死掉
                print(f"[collector] 采样异常: {exc}")
            time.sleep(self.interval)

    def start(self):
        # 先同步采一次，保证服务一启动就有数据
        self._sample_once()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()


# ---------------------------------------------------------------- 工具函数
def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:7.1f}{unit}"
        n /= 1024


def human_uptime(seconds):
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}天")
    if h:
        parts.append(f"{h}时")
    parts.append(f"{m}分")
    return "".join(parts)


# 直接运行本文件时，充当一个迷你 top，验证采集是否正确
if __name__ == "__main__":
    c = MetricsCollector()
    c.start()
    try:
        while True:
            time.sleep(1)
            s = c.snapshot
            os.system("clear")
            print(f"PulseWatch  {s['hostname']} | {s['os']} | 内核 {s['kernel']}")
            print(f"运行 {human_uptime(s['uptime_s'])}   "
                  f"负载 {s['loadavg']}   {s['cpu_count']} 核")
            print("-" * 64)
            print(f"CPU  {s['cpu_percent']:5.1f}%   "
                  f"每核: {' '.join(str(x) for x in s['per_cpu'])}")
            m = s["mem"]
            print(f"内存 {m['percent']:5.1f}%   "
                  f"{human_bytes(m['used'])} / {human_bytes(m['total'])}")
            d = s["disk"]
            print(f"磁盘 {d['percent']:5.1f}%   "
                  f"{human_bytes(d['used'])} / {human_bytes(d['total'])}")
            n = s["net"]
            print(f"网络 ↓ {human_bytes(n['rx_rate'])}/s  "
                  f"↑ {human_bytes(n['tx_rate'])}/s")
            print("-" * 64)
            print(f"{'PID':>7}  {'CPU%':>6}  {'内存MB':>8}  进程")
            for p in s["procs"]:
                print(f"{p['pid']:>7}  {p['cpu']:>6.1f}  "
                      f"{p['mem_mb']:>8.1f}  {p['name']}")
    except KeyboardInterrupt:
        print("\n已停止")
