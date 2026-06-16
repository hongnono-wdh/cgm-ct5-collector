"""抓取 CT5 全部 0x37 批量帧的原始字节，存到 raw_batches.txt（每行: startId<TAB>hex）。
不做解析，纯采集，供离线多方法分析。
用法: python ct5_capture_raw.py
"""
import asyncio
import datetime
from bleak import BleakClient, BleakScanner

WRITE = "00001002-1212-efde-1523-785feabcd123"
NOTIFY = "00001001-1212-efde-1523-785feabcd123"
OUT = "raw_batches.txt"   # 相对当前工作目录(在项目根目录运行)
from _config import PHONE  # 真实手机号来自环境变量 CT5_PHONE 或 config.local.json
COUNT = 45


def derive(p):
    s = p + "0" * (12 - len(p)) if len(p) < 12 else p[:12]
    s = "1" + s[1:] if s.startswith("0") else s
    return [ord(c) - 48 for c in s[8:12]]


def rsum(b):
    return sum(b) & 0xFF


def frame(cmd, payload):
    body = [cmd] + list(payload)
    return bytes(body + [rsum(body)])


async def main():
    randB = derive(PHONE)
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: "anytime" in ((a.local_name or d.name or "").lower()), timeout=25.0)
    if not dev:
        print("没扫到设备。"); return
    pending = {"b": None}
    lines = []
    async with BleakClient(dev, timeout=25.0) as client:
        print(f"已连接 {dev.address}")

        def handler(_c, data):
            b = bytes(data)
            if b and b[0] == 0x37:
                pending["b"] = b

        await client.start_notify(NOTIFY, handler)
        await client.write_gatt_char(WRITE, frame(0x31, randB), response=False)
        await asyncio.sleep(1.2)
        n = datetime.datetime.now()
        await client.write_gatt_char(WRITE, frame(0x03, [n.year - 1900, n.month, n.day, n.hour, n.minute, n.second]), response=False)
        await asyncio.sleep(1.2)

        start = 1
        for _ in range(60):
            pending["b"] = None
            await client.write_gatt_char(WRITE, frame(0x37, [start & 0xFF, (start >> 8) & 0xFF, COUNT]), response=False)
            for _w in range(30):
                if pending["b"] is not None:
                    break
                await asyncio.sleep(0.1)
            b = pending["b"]
            if b is None:
                print(f"id={start} 无响应,停"); break
            lines.append(f"{start}\t{b.hex()}")
            sid = b[1] + (b[2] << 8)
            print(f"id={start} (帧startId={sid}) 长度={len(b)}B")
            # 用长度判断是否拉完: 满批通常 >=400B；明显变短则可能是最后一批，再多拉一次确认
            if len(b) < 60:
                break
            # 估算本批记录数推进 startId
            recs15 = (len(b) - 4) // 15
            start = sid + recs15
            if recs15 == 0:
                break
        with open(OUT, "w") as f:
            f.write("\n".join(lines))
        print(f"\n已存 {len(lines)} 帧原始数据 → {OUT}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"出错: {e!r}")
