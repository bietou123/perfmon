# -*- coding: utf-8 -*-
"""Windows 性能数据采集：CPU / 内存 / 磁盘 / 网络 / GPU。

指标口径尽量对齐 Windows 任务管理器：
- CPU 利用率用 PDH 的 Processor Utility（考虑睿频后的实际占用）
- CPU 速度 = 基准频率 x (% Processor Performance / 100)，与任务管理器一致
- 进程/线程/句柄用 PDH System / Process(_Total) 计数器，避免遍历 400+ 进程
"""
import ctypes
import ctypes.wintypes as wt
import json
import socket
import subprocess
import threading
import time
import winreg
from collections import deque

import psutil

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_pdh = ctypes.WinDLL("pdh.dll")

PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    # CStatus(4) + 4 字节对齐填充 + double(offset 8)
    _fields_ = [("CStatus", wt.DWORD), ("_pad", wt.DWORD),
                ("doubleValue", ctypes.c_double)]


class PDH_FMT_COUNTERVALUE_ITEM(ctypes.Structure):
    _fields_ = [("szName", wt.LPWSTR), ("FmtValue", PDH_FMT_COUNTERVALUE)]


_P = ctypes.c_void_p
_pdh.PdhOpenQueryW.argtypes = [wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(_P)]
_pdh.PdhOpenQueryW.restype = wt.LONG
_pdh.PdhAddEnglishCounterW.argtypes = [_P, wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(_P)]
_pdh.PdhAddEnglishCounterW.restype = wt.LONG
_pdh.PdhCollectQueryData.argtypes = [_P]
_pdh.PdhCollectQueryData.restype = wt.LONG
_pdh.PdhGetFormattedCounterValue.argtypes = [
    _P, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(PDH_FMT_COUNTERVALUE)]
_pdh.PdhGetFormattedCounterValue.restype = wt.LONG
_pdh.PdhGetFormattedCounterArrayW.argtypes = [
    _P, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
_pdh.PdhGetFormattedCounterArrayW.restype = wt.LONG


class PdhQuery:
    """极轻量的 PDH 查询封装，每次 tick 只做一次 CollectQueryData。"""

    def __init__(self):
        self.q = _P()
        if _pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.q)) != 0:
            raise OSError("PdhOpenQueryW failed")
        self._counters = {}

    def add(self, key, path):
        c = _P()
        if _pdh.PdhAddEnglishCounterW(self.q, path, 0, ctypes.byref(c)) != 0:
            return False
        self._counters[key] = c
        return True

    def collect(self):
        return _pdh.PdhCollectQueryData(self.q) == 0

    def value(self, key, default=0.0):
        c = self._counters.get(key)
        if c is None:
            return default
        v = PDH_FMT_COUNTERVALUE()
        if _pdh.PdhGetFormattedCounterValue(c, PDH_FMT_DOUBLE, None, ctypes.byref(v)) != 0:
            return default
        if v.CStatus != 0:
            return default
        return float(v.doubleValue)

    def array(self, key):
        """返回 [(实例名, 值), ...]，用于 PhysicalDisk / Network 的逐实例数据。"""
        c = self._counters.get(key)
        if c is None:
            return []
        size = wt.DWORD(0)
        count = wt.DWORD(0)
        rc = _pdh.PdhGetFormattedCounterArrayW(
            c, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
        # PdhGetFormattedCounterArrayW 返回 signed LONG，PDH_MORE_DATA 高位为 1
        if (rc & 0xFFFFFFFF) != PDH_MORE_DATA or size.value == 0:
            return []
        buf = ctypes.create_string_buffer(size.value)
        rc = _pdh.PdhGetFormattedCounterArrayW(
            c, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf)
        if rc != 0:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM))
        out = []
        for i in range(count.value):
            it = items[i]
            if it.FmtValue.CStatus != 0:
                continue
            out.append((it.szName or "", float(it.FmtValue.doubleValue)))
        return out


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

    def __init__(self):
        super().__init__()
        self.dwLength = ctypes.sizeof(self)


def memory_status():
    st = MEMORYSTATUSEX()
    try:
        if _k32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 静态硬件信息
def _read_reg(path, name):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _count_subkeys(path):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            n = 0
            while True:
                try:
                    winreg.EnumKey(k, n)
                except OSError:
                    break
                n += 1
            return n
    except OSError:
        return 0


def _cache_sizes():
    """汇总 L1(数据) / L2 / L3 缓存总字节数，跳过指令缓存与 Trace 缓存。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetLogicalProcessorInformationEx.argtypes = [
        ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetLogicalProcessorInformationEx.restype = ctypes.c_int

    RelationCache = 2
    size = ctypes.c_ulong(0)
    kernel32.GetLogicalProcessorInformationEx(
        RelationCache, None, ctypes.byref(size))
    if size.value == 0:
        return {}
    buf = ctypes.create_string_buffer(size.value)
    if not kernel32.GetLogicalProcessorInformationEx(
            RelationCache, buf, ctypes.byref(size)):
        return {}
    totals, off = {}, 0
    while off + 8 <= size.value:
        rel_type = ctypes.c_uint.from_buffer(buf, off).value
        rel_len = ctypes.c_uint.from_buffer(buf, off + 4).value
        if rel_len <= 0:
            break
        if rel_type == RelationCache and off + 20 <= size.value:
            level = ctypes.c_ubyte.from_buffer(buf, off + 8).value
            cache_size = ctypes.c_uint.from_buffer(buf, off + 12).value
            # L1 同时计入数据与指令缓存（任务管理器的 512KB 就是两者之和）
            totals[level] = totals.get(level, 0) + cache_size
        off += rel_len
    return totals


def _ps_json(script):
    """启动时跑一次 PowerShell 拿 WMI 数据，失败返回 None。"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$OutputEncoding=[Text.Encoding]::UTF8; " + script],
            capture_output=True, timeout=12,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        text = proc.stdout.decode("utf-8", "ignore").strip()
        if not text:
            try:
                text = proc.stdout.decode("gbk", "ignore").strip()
            except Exception:
                return None
        return json.loads(text) if text else None
    except Exception:
        return None


def static_info():
    info = {
        "cpu_name": "Unknown CPU",
        "cpu_base_mhz": 0,
        "sockets": 1,
        "physical_cores": psutil.cpu_count(logical=False) or 0,
        "logical_cores": psutil.cpu_count(logical=True) or 0,
        "virt": "—",
        "l1": 0, "l2": 0, "l3": 0,
        "mem_total": psutil.virtual_memory().total,
        "mem_speed": 0, "mem_slots": 0, "mem_used_slots": 0, "mem_form": "—",
        "disks": {},
        "gpu_name": "—", "gpu_total_mem": 0, "gpu_driver": "—",
    }

    name = _read_reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                     "ProcessorNameString")
    if name:
        info["cpu_name"] = " ".join(str(name).split())

    mhz = _read_reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "~MHz")
    if isinstance(mhz, int) and mhz > 0:
        info["cpu_base_mhz"] = mhz

    sockets = _count_subkeys(r"HARDWARE\DESCRIPTION\System\CentralProcessor")
    # 该键下每个逻辑处理器一个子键，不能当插槽数；插槽数由 WMI 补
    if sockets:
        info["sockets"] = 0

    try:
        c = _cache_sizes()
        info["l1"] = c.get(1, 0)
        info["l2"] = c.get(2, 0)
        info["l3"] = c.get(3, 0)
    except Exception:
        pass

    # 虚拟化状态（Hyper-V 是否启用）
    for key in (r"SYSTEM\CurrentControlSet\Control\DeviceGuard",):
        v = _read_reg(key, "EnableVirtualizationBasedSecurity")
        if v == 1:
            info["virt"] = "已启用"
    if info["virt"] == "—":
        try:
            if psutil.cpu_count(logical=True) and _read_reg(
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                    "VMMonitorModeExtensions") == 1:
                info["virt"] = "已启用"
            else:
                info["virt"] = "已禁用"
        except Exception:
            pass

    wmi = _ps_json(
        "$m=@(Get-CimInstance Win32_PhysicalMemory | "
        "Select-Object Capacity,Speed,FormFactor); "
        "$d=@(Get-CimInstance Win32_DiskDrive | "
        "Select-Object Model,MediaType,Size,Index,InterfaceType); "
        "$c=Get-CimInstance Win32_ComputerSystem; "
        "[pscustomobject]@{mem=$m;disk=$d;sockets=$c.NumberOfProcessors} "
        "| ConvertTo-Json -Depth 5 -Compress"
    )
    if wmi:
        if wmi.get("sockets"):
            info["sockets"] = int(wmi["sockets"])
        mem = wmi.get("mem") or []
        if isinstance(mem, dict):
            mem = [mem]
        if mem:
            info["mem_slots"] = len(mem)
            info["mem_used_slots"] = len(mem)
            speeds = [int(m.get("Speed") or 0) for m in mem]
            info["mem_speed"] = max(speeds) if speeds else 0
            ff = mem[0].get("FormFactor")
            info["mem_form"] = {8: "DIMM", 12: "SODIMM", 13: "SODIMM",
                                11: "RIMM", 15: "FB-DIMM"}.get(ff, "DIMM")

        disk = wmi.get("disk") or []
        if isinstance(disk, dict):
            disk = [disk]
        for d in disk:
            model = (d.get("Model") or "").strip()
            media = (d.get("MediaType") or "").lower()
            iface = (d.get("InterfaceType") or "").strip()
            is_ssd = "ssd" in media or "solid" in media or iface.upper() in (
                "NVME", "SCSI")
            if "nvme" in model.lower() or iface.upper() == "NVME":
                bus = "NVMe"
            else:
                bus = iface or ("SATA" if is_ssd else "")
            info["disks"][int(d.get("Index") or 0)] = {
                "model": model,
                "type": "SSD" if is_ssd else "HDD",
                "bus": bus,
                "size": int(d.get("Size") or 0),
            }
    return info


def gpu_info_static():
    out = {"gpu_name": "—", "gpu_total_mem": 0, "gpu_driver": "—"}
    try:
        p = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=6, creationflags=0x08000000,
        )
        line = p.stdout.decode("utf-8", "ignore").strip().splitlines()
        if line:
            parts = [x.strip() for x in line[0].split(",")]
            if len(parts) >= 3:
                out["gpu_name"] = parts[0].replace("NVIDIA ", "")
                out["gpu_total_mem"] = int(float(parts[1])) * 1024 * 1024
                out["gpu_driver"] = parts[2]
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 实时采集
class Collector(threading.Thread):
    INTERVAL = 0.5
    HISTORY = 120  # 60 秒

    def __init__(self):
        super().__init__(daemon=True, name="collector")
        self.lock = threading.Lock()
        self.snapshot = {}
        self.history = {"cpu": deque(maxlen=self.HISTORY),
                        "mem": deque(maxlen=self.HISTORY),
                        "net_rx": deque(maxlen=self.HISTORY),
                        "net_tx": deque(maxlen=self.HISTORY),
                        "gpu": deque(maxlen=self.HISTORY)}
        self.disk_hist = {}   # "C:" -> deque
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.static = {}
        self._gpu_last = 0.0
        self._gpu_state = {"util": 0, "temp": 0, "used": 0, "total": 0}
        self._wifi_last = 0.0
        self._wifi = {}
        self._iface = {}
        self._iface_last = 0.0
        self._io_prev = None
        self._io_time = 0.0

    # --- 启动预热 -------------------------------------------------
    def prepare(self):
        psutil.cpu_percent()              # 第一次调用恒为 0，先吃掉
        psutil.cpu_percent(percpu=True)
        self.static = static_info()
        self.static.update(gpu_info_static())
        self.q = PdhQuery()
        for key, path in (
            ("cpu_util", r"\Processor Information(_Total)\% Processor Utility"),
            ("cpu_perf", r"\Processor Information(_Total)\% Processor Performance"),
            ("cpu_time", r"\Processor Information(_Total)\% Processor Time"),
            ("proc_time", r"\Processor(_Total)\% Processor Time"),
            ("threads", r"\System\Threads"),
            ("processes", r"\System\Processes"),
            ("handles", r"\Process(_Total)\Handle Count"),
            ("cache_normal", r"\Memory\Standby Cache Normal Priority Bytes"),
            ("cache_core", r"\Memory\Standby Cache Core Bytes"),
            ("cache_reserve", r"\Memory\Standby Cache Reserve Bytes"),
            ("disk_time", r"\PhysicalDisk(*)\% Disk Time"),
            ("disk_read", r"\PhysicalDisk(*)\Disk Read Bytes/sec"),
            ("disk_write", r"\PhysicalDisk(*)\Disk Write Bytes/sec"),
        ):
            self.q.add(key, path)
        self._net_prev = psutil.net_io_counters()
        self._net_time = time.time()
        self._partitions = [p for p in psutil.disk_partitions(all=False)]
        self._refresh_gpu(force=True)
        self._refresh_wifi(force=True)
        self._iface = self._iface_info()
        self._ready.set()

    # --- GPU / Wi-Fi（低频，避免子进程拖慢主循环）------------------
    def _refresh_gpu(self, force=False):
        now = time.time()
        if not force and now - self._gpu_last < 2.0:
            return
        self._gpu_last = now
        try:
            p = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, timeout=5, creationflags=0x08000000,
            )
            line = p.stdout.decode("utf-8", "ignore").strip().splitlines()
            if line:
                g = [x.strip() for x in line[0].split(",")]
                self._gpu_state = {
                    "util": int(float(g[0])),
                    "temp": int(float(g[1])),
                    "used": int(float(g[2])) * 1024 * 1024,
                    "total": int(float(g[3])) * 1024 * 1024,
                }
        except Exception:
            pass

    def _refresh_wifi(self, force=False):
        now = time.time()
        if not force and now - self._wifi_last < 5.0:
            return
        self._wifi_last = now
        try:
            p = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                               capture_output=True, timeout=5,
                               creationflags=0x08000000)
            raw = p.stdout
            text = ""
            for enc in ("gbk", "utf-8"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            keys = {
                "ssid": ("SSID",),
                "signal": ("信号", "Signal"),
                "rx_rate": ("接收速率(Mbps)", "Receive rate (Mbps)"),
                "tx_rate": ("传输速率(Mbps)", "Transmit rate (Mbps)"),
                "state": ("状态", "State"),
                "bssid": ("BSSID",),
                "radio": ("无线电类型", "Radio type"),
                "auth": ("身份验证", "Authentication"),
            }
            data = {}
            for line in text.splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                # 排除 "BSSID 1" 这类带编号的噪声
                for slot, names in keys.items():
                    if k in names:
                        data.setdefault(slot, v)
            self._wifi = data
        except Exception:
            pass

    def _iface_info(self):
        """在线网卡的名称与 IPv4 地址。"""
        try:
            stats = psutil.net_if_stats()
            for name, addrs in psutil.net_if_addrs().items():
                st = stats.get(name)
                if not st or not st.isup:
                    continue
                for a in addrs:
                    if a.family == socket.AF_INET and not a.address.startswith("127."):
                        return {"name": name, "ip": a.address}
        except Exception:
            pass
        return {}

    # --- 主循环 ---------------------------------------------------
    def run(self):
        try:
            self.prepare()
        except Exception as exc:
            print("prepare failed:", exc)
            self._ready.set()
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._tick()
            except Exception as exc:
                print("tick failed:", exc)
            dt = self.INTERVAL - (time.time() - t0)
            if dt > 0:
                self._stop.wait(dt)

    def stop(self):
        self._stop.set()

    def _tick(self):
        self.q.collect()
        now = time.time()

        # ---- CPU ----
        # 总利用率用 psutil(GetSystemTimes)，口径稳定；PDH 的 Processor Utility
        # 在睿频时会突破 100%，容易长期顶格，只用来推算实际频率
        perf = self.q.value("cpu_perf")
        util = psutil.cpu_percent()
        util = max(0.0, min(100.0, util))

        base_ghz = self.static.get("cpu_base_mhz", 0) / 1000.0
        speed = base_ghz * perf / 100.0 if perf > 0 else 0.0
        if speed <= 0:
            f = psutil.cpu_freq()
            speed = (f.current / 1000.0) if f and f.current else base_ghz

        processes = int(self.q.value("processes")) or len(psutil.pids())
        threads = int(self.q.value("threads")) or 0
        handles = int(self.q.value("handles")) or 0

        per_core = [max(0.0, min(100.0, x)) for x in psutil.cpu_percent(percpu=True)]

        # ---- 内存 ----
        vm = psutil.virtual_memory()
        ms = memory_status()
        cached = (self.q.value("cache_normal") + self.q.value("cache_core")
                  + self.q.value("cache_reserve"))
        mem = {
            "total": vm.total,
            "used": vm.total - vm.available,
            "free": vm.available,
            "percent": vm.percent,
            "committed": (ms.ullTotalPageFile - ms.ullAvailPageFile) if ms else 0,
            "commit_limit": ms.ullTotalPageFile if ms else 0,
            "cached": cached,
        }

        # ---- 磁盘 ----
        disks = {}
        try:
            time_vals = dict(self.q.array("disk_time"))
            read_vals = dict(self.q.array("disk_read"))
            write_vals = dict(self.q.array("disk_write"))
            letter_map = {}
            for inst, active in time_vals.items():
                if inst.startswith("_"):
                    continue
                head, _, letter = inst.partition(" ")
                try:
                    idx = int(head)
                except ValueError:
                    continue
                entry = {
                    "index": idx,
                    "letter": letter,
                    "label": f"磁盘 {idx}" + (f" ({letter})" if letter else ""),
                    "active": max(0.0, min(100.0, active)),
                    "read": max(0.0, read_vals.get(inst, 0.0)),
                    "write": max(0.0, write_vals.get(inst, 0.0)),
                }
                disks[letter or f"#{idx}"] = entry
                if letter:
                    letter_map[letter] = entry

            for part in self._partitions:
                drive = part.device.rstrip("\\")
                entry = letter_map.get(drive)
                if entry is None:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    entry["total"] = usage.total
                    entry["used"] = usage.used
                    entry["percent"] = usage.percent
                    entry["filesystem"] = part.fstype
                except OSError:
                    pass
        except Exception:
            pass

        # ---- 网络 ----
        nc = psutil.net_io_counters()
        dt = max(1e-3, now - self._net_time)
        rx_rate = max(0.0, (nc.bytes_recv - self._net_prev.bytes_recv) / dt)
        tx_rate = max(0.0, (nc.bytes_sent - self._net_prev.bytes_sent) / dt)
        self._net_prev, self._net_time = nc, now
        if now - self._iface_last > 10.0:
            self._iface_last = now
            self._iface = self._iface_info()

        self._refresh_gpu()
        self._refresh_wifi()

        snap = {
            "ts": now,
            "cpu": {
                "usage": round(util, 1),
                "speed": round(speed, 2),
                "processes": processes,
                "threads": threads,
                "handles": handles,
                "per_core": [round(x, 1) for x in per_core],
                "uptime": time.time() - psutil.boot_time(),
            },
            "mem": mem,
            "disks": disks,
            "net": {
                "rx": rx_rate, "tx": tx_rate,
                "wifi": dict(self._wifi),
                "iface": dict(self._iface),
            },
            "gpu": {
                "util": self._gpu_state["util"],
                "temp": self._gpu_state["temp"],
                "used": self._gpu_state["used"],
                "total": self._gpu_state["total"] or self.static.get(
                    "gpu_total_mem", 0),
            },
        }

        with self.lock:
            self.snapshot = snap
            self.history["cpu"].append(snap["cpu"]["usage"])
            self.history["mem"].append(mem["percent"])
            self.history["net_rx"].append(rx_rate)
            self.history["net_tx"].append(tx_rate)
            self.history["gpu"].append(snap["gpu"]["util"])
            for key, d in disks.items():
                hist = self.disk_hist.get(key)
                if hist is None:
                    hist = self.disk_hist[key] = {
                        "active": deque(maxlen=self.HISTORY),
                        "read": deque(maxlen=self.HISTORY),
                        "write": deque(maxlen=self.HISTORY),
                    }
                hist["active"].append(d["active"])
                hist["read"].append(d["read"])
                hist["write"].append(d["write"])

    # --- 输出 -----------------------------------------------------
    def payload(self):
        with self.lock:
            snap = dict(self.snapshot)
            hist = {k: list(v) for k, v in self.history.items()}
            dhist = {k: {kk: list(vv) for kk, vv in v.items()}
                     for k, v in self.disk_hist.items()}
        return {"current": snap, "history": hist, "disk_history": dhist,
                "static": self.static}
