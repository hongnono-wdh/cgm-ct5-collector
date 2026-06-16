"""鱼跃 CGM CT5 长时间实时血糖记录。
握手(checkId+setDate) → 用已锁定 cipher 解密推送 → 写 CSV + 控制台打印。
用法: python ct5_logger.py [cipher,默认124] [监听秒数,默认1800]
"""
import asyncio
import sys
import datetime
import csv
import os
from bleak import BleakClient, BleakScanner

WRITE = "00001002-1212-efde-1523-785feabcd123"
NOTIFY = "00001001-1212-efde-1523-785feabcd123"
CSV_PATH = "glucose_log.csv"   # 相对当前工作目录(在项目根目录运行)
PHONE = "13800138000"

CIPHER = int(sys.argv[1]) if len(sys.argv) > 1 else 124
LISTEN = int(sys.argv[2]) if len(sys.argv) > 2 else 1800
CALIB = 1.327   # 标定系数,对齐App显示值(见 rebuild_csv.py 注释)


def derive(phone):
    s = phone + "0" * (12 - len(phone)) if len(phone) < 12 else phone[:12]
    if s.startswith("0"):
        s = "1" + s[1:]
    return [ord(c) - 48 for c in s[4:8]], [ord(c) - 48 for c in s[8:12]]


def rsum(b):
    return sum(b) & 0xFF


def frame(cmd, payload):
    body = [cmd] + list(payload)
    return bytes(body + [rsum(body)])


def encode(data, cipher):
    bits = "".join(f"{(x ^ cipher) & 0xFF:08b}" for x in data)
    bl = list(bits)
    for i in range(len(bl) - 1):
        if bl[i + 1] == "0":
            bl[i] = "1" if bl[i] == "0" else "0"
    return bytes(int("".join(bl)[i:i + 8], 2) for i in range(0, len(bl), 8))


def parse(fr, cipher):
    buf = bytes(fr[:3]) + encode(fr[3:-1], cipher)
    if len(buf) < 11:
        return None
    return dict(gid=buf[1] + (buf[2] << 8),
                ib=round(((buf[3] << 8) | buf[4]) / 100, 2),
                iw=round(((buf[5] << 8) | buf[6]) / 100, 2),
                T=round((buf[7] - 40) + buf[8] / 100, 2),
                glu_mg=((buf[9] & 0x0F) << 8) + buf[10],
                trend=buf[9] >> 4)


def log_csv(r):
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "glucoseId", "glu_mmol", "glu_mg", "Ib", "Iw", "T", "trend"])
        cal_mg = round(r["glu_mg"] * CALIB)
        w.writerow([datetime.datetime.now().isoformat(timespec="seconds"),
                    r["gid"], round(cal_mg / 18, 1), cal_mg, r["ib"], r["iw"], r["T"], r["trend"]])


async def main():
    randA, randB = derive(PHONE)
    print(f"cipher={CIPHER} 监听={LISTEN}s  RandomA={randA} RandomB={randB}")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: "anytime" in ((a.local_name or d.name or "").lower()), timeout=25.0)
    if not dev:
        print("没扫到设备。"); return
    async with BleakClient(dev, timeout=25.0) as client:
        print(f"已连接 {dev.address}")

        def handler(_c, data):
            b = bytes(data)
            cmd = b[0] if b else -1
            ok = len(b) >= 2 and b[-1] == rsum(b[:-1])
            if cmd == 0x31:
                print(f"checkId {'OK认证' if (len(b) > 5 and b[5] == 1) else 'FAIL'}")
                return
            if len(b) >= 15 and ok:
                r = parse(b, CIPHER)
                if r and 20 <= r["glu_mg"] <= 450:
                    ts = datetime.datetime.now().strftime("%H:%M:%S")
                    cal_mg = round(r["glu_mg"] * CALIB)
                    print(f"[{ts}] ★ {round(cal_mg/18,1)} mmol/L ({cal_mg} mg/dL, 原始{r['glu_mg']}) "
                          f"id={r['gid']} Iw={r['iw']} Ib={r['ib']} T={r['T']}℃ trend={r['trend']}")
                    log_csv(r)
                else:
                    print(f"  收到帧但数值存疑: {b.hex(' ')}")
                asyncio.create_task(client.write_gatt_char(WRITE, bytes([0x35, 0x55, 0xAA, 0x34]), response=False))

        await client.start_notify(NOTIFY, handler)
        await client.write_gatt_char(WRITE, frame(0x31, randB), response=False)
        await asyncio.sleep(1.5)
        n = datetime.datetime.now()
        await client.write_gatt_char(WRITE, frame(0x03, [n.year - 1900, n.month, n.day, n.hour, n.minute, n.second]), response=False)
        print(f"握手完成,监听 {LISTEN}s (每~5分钟一帧)...")
        await asyncio.sleep(LISTEN)
        print("结束。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"出错: {e!r}")
