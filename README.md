# 性能监视器 Perfmon

一个仿 Windows 任务管理器「性能」页的本地小工具：一屏看完 CPU、内存、磁盘、网络、GPU 的实时曲线与明细。后端用 `ctypes` 直接调 PDH（性能数据助手）计数器，和任务管理器同一套数据口径；前端是纯 HTML/CSS/Canvas，没有构建步骤。

## 功能

目前只实现「性能」页，其余页签是占位。

| 类别 | 内容 |
| --- | --- |
| CPU | 总利用率、实际频率（基准频率 × `% Processor Performance`）、进程数 / 线程数 / 句柄数、每逻辑核心利用率、开机时长 |
| 内存 | 使用量 / 总量、已提交与提交上限、待机缓存（Normal / Core / Reserve 三项之和） |
| 磁盘 | 每块物理磁盘的活动时间、读写速率、容量与文件系统；60 秒历史曲线 |
| 网络 | 全局上下行速率、在线网卡与 IPv4、Wi-Fi 的 SSID / 信号 / 速率 / 频段 / 认证方式 |
| GPU | NVIDIA 显卡利用率、温度、显存占用（通过 `nvidia-smi`；没有时显示 —） |

顶部有窗口置顶、最小化、最大化、关闭按钮；「运行新任务」弹窗等价于任务管理器的同名功能。

## 实现要点

- **PDH 计数器**：`collector.py` 用 `ctypes` 调 `PdhOpenQueryW` / `PdhAddEnglishCounterW` / `PdhGetFormattedCounterArrayW`。英文计数器名让系统语言不影响取数。进程数、线程数、句柄数直接取 `System\*` 和 `Process(_Total)\Handle Count`，避免每秒遍历几百个进程。
- **CPU 利用率**：用 `psutil.cpu_percent()`（底层 `GetSystemTimes`）保证不长期顶格；PDH 的 `% Processor Utility` 在睿频时会超过 100%，只用来反推实际频率。
- **采样频率**：主循环 0.5 秒，历史保留 120 个点（60 秒）。GPU 每 2 秒、Wi-Fi 每 5 秒刷新一次，子进程放后台，不拖慢主循环。
- **进程对象缓存**：`Collector` 复用同一个 PDH 查询句柄，所有状态在采样线程里更新，HTTP 线程只读快照。

## 安全边界

- HTTP 服务只绑定 `127.0.0.1`，端口由系统随机分配。
- 静态文件服务做了 `normpath` + 前缀校验，越界路径返回 404。
- **「运行新任务」会以当前用户权限直接执行输入的命令**（`subprocess.Popen(cmd, shell=True)`），和任务管理器行为一致。不要在里面粘贴来路不明的命令。
- 程序不联网、不上传数据；`nvidia-smi` 与 `netsh wlan show interfaces` 都是本机命令。

## 快速开始

```powershell
pip install -r requirements.txt
python app.py
```

需要 Windows 10 / 11、Python 3.9+、WebView2 运行时（Win11 自带）。也会退回默认浏览器打开界面，功能不变。

想双击无控制台启动，用仓库里的 `run.vbs`（调用 `pythonw.exe app.py`）。

## 离线验证

```powershell
python tools\smoke_test.py
```

不创建窗口：直接构造采集器采样两次，校验 `/api/stats`、`/`、`/app.js`、`/style.css` 和越界 404。输出为纯 ASCII，避免 Windows 控制台编码问题。

## 目录结构

```
app.py              HTTP 服务 + pywebview 窗口 + 窗口控制 API
collector.py        PDH 计数器封装、静态硬件信息、采样线程
web/
  index.html        页面骨架
  style.css         深色/浅色跟随系统的界面样式
  app.js            曲线绘制、磁贴与明细渲染
tools/
  smoke_test.py     无窗口离线冒烟
run.vbs             无控制台启动
```

## 已知限制

- 只支持 Windows；PDH 计数器名固定为英文。
- 只实现了「性能」页，进程 / 应用历史 / 启动应用 / 用户 / 详细信息 / 服务为占位。
- GPU 只认 NVIDIA（走 `nvidia-smi`）；AMD / Intel 核显显示 —。
- Wi-Fi 信息依赖中文或英文的 `netsh` 输出，其他语言系统可能显示不全。
- Windows 上通常读不到 CPU 温度，未做温度采集。
- 仅在 Windows 11 上实测；无管理员权限时功能完整，因为不做系统级修改。

## 许可证

MIT，见 [LICENSE](LICENSE)。
