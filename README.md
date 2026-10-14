# PulseWatch · Linux 脉搏监控

一个**零第三方依赖**的 Linux 系统监控程序：采集器直接读取内核的 `/proc`
台账，内置 Web 服务把实时数据推给浏览器仪表盘。纯 Python 标准库实现。

## 架构

```
Linux 内核 (/proc/stat, /proc/meminfo, /proc/net/dev, /proc/<pid>/ ...)
        │  直接读取（这就是"操作系统把内部状态以文件形式公开"）
        ▼
collector.py   后台线程每秒采样一次，两次采样做差算速率
        │
        ▼
server.py      标准库 http.server，提供 /api/metrics（JSON）
        │  浏览器每秒拉取
        ▼
static/index.html  实时大屏：CPU 曲线 / 每核占用 / 内存 / 磁盘 / 网络 / Top 进程
```

## 运行

```bash
# 在 WSL / 任意 Linux 中
cd pulsewatch
python3 server.py
# 然后浏览器打开 http://localhost:8765
```

终端模式（迷你 top，不开网页时用）：

```bash
python3 collector.py
```

自测（含断言，可配合 `yes > /dev/null &` 制造 CPU 负载验证）：

```bash
python3 selftest.py
```

> WSL2 会自动把端口转发到 Windows，所以服务跑在 WSL 里，
> Windows 浏览器直接访问 localhost:8765 即可。
> 重启电脑或 `wsl --shutdown` 后需要重新运行 `python3 server.py`。

## 文件说明

| 文件 | 职责 |
|---|---|
| `collector.py` | 指标采集：CPU/内存/磁盘/网络/进程，可独立终端运行 |
| `server.py` | HTTP 服务 + JSON API + 静态页面托管 |
| `static/index.html` | 单文件仪表盘（原生 JS + Canvas，无框架无 CDN） |
| `selftest.py` | 采集正确性自测 |

## 读了哪些"内核台账"

| 文件 | 读到什么 |
|---|---|
| `/proc/stat` | CPU 各模式累计滴答数（两次做差算占用率） |
| `/proc/meminfo` | 内存 / 可用内存 / Swap |
| `/proc/net/dev` | 网卡累计收发包字节（做差算速率） |
| `/proc/<pid>/stat` | 每个进程的 CPU 滴答、常驻内存页数 |
| `/proc/uptime` | 系统运行时长 |
| `os.statvfs("/")` | 磁盘总量/剩余 |
| `os.getloadavg()` | 1/5/15 分钟平均负载 |

## 下一步可加的功能

- [ ] 历史落盘（SQLite），支持回看过去一小时/一天
- [ ] CPU/内存超阈值报警（写入日志 / Webhook 推送）
- [ ] 多台机器集中面板（每台机器装一个 agent）
- [ ] systemd service 单元：开机自启、崩溃重启
- [ ] 磁盘各挂载点、磁盘 IO、TCP 连接数、容器视图
