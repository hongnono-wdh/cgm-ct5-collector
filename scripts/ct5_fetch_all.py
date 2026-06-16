"""鱼跃 CT5 全历史抓取 —— fastpull 分批拉完传感器存储的全部血糖，写 glucose_log.csv。
按官方 App pullResponseFromTransmitter_CT5 精确解析(11字节/记录)。
用法: python ct5_fetch_all.py [interval秒,默认180]  (用于按id反推时间轴)
"""
import asyncio
import sys
import datetime
import csv
from bleak import BleakClient, BleakScanner

WRITE = "00001002-1212-efde-1523-785feabcd123"
NOTIFY = "00001001-1212-efde-1523-785feabcd123"
CSV_PATH = "glucose_log.csv"   # 相对当前工作目录(在项目根目录运行)
from _config import PHONE, CIPHER, CALIB  # 隐私/标定来自环境变量 CT5_* 或 config.local.json
COUNT = 45
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 180


def derive(p):
    s = p + "0" * (12 - len(p)) if len(p) < 12 else p[:12]
    s = "1" + s[1:] if s.startswith("0") else s
    return [ord(c) - 48 for c in s[8:12]]


def rsum(b):
    return sum(b) & 0xFF


def frame(cmd, payload):
    body = [cmd] + list(payload)
    return bytes(body + [rsum(body)])


def encode(data, c):
    bits = "".join(f"{(x ^ c) & 0xFF:08b}" for x in data)
    bl = list(bits)
    for i in range(len(bl) - 1):
        if bl[i + 1] == "0":
            bl[i] = "1" if bl[i] == "0" else "0"
    return bytes(int("".join(bl)[i:i + 8], 2) for i in range(0, len(bl), 8))


def clean_blocks(enc):
    """移除 11 字节全 FC(空槽)/全 FF(无效) 的块（在加密流上，按 §3.2）。"""
    out = bytearray()
    i = 0
    while i < len(enc):
        chunk = enc[i:i + 11]
        if len(chunk) == 11 and (all(x == 0xFC for x in chunk) or all(x == 0xFF for x in chunk)):
            i += 11
            continue
        out.append(enc[i])
        i += 1
    return bytes(out)


def parse_batch(fr, cipher):
    """解析 0x37 批量帧 → [(gid, glu_mg, glu_mmol, ib, iw, T, trend, err)]"""
    if fr[0] != 0x37 or fr[-1] != rsum(fr[:-1]):
        return []
    start_id = fr[1] + (fr[2] << 8)
    payload = encode(bytes(fr[3:-1]), cipher)   # 不做 clean_blocks（会破坏15B对齐）
    recs = []
    REC = 15 if len(payload) % 15 == 0 else 11   # 电压模式15字节/普通11字节
    n = len(payload) // REC
    for i in range(n):
        r = payload[i * REC:i * REC + 9]
        if len(r) < 9:
            break
        if all(x == 0x7b for x in r):            # 空槽标志=真实数据边界
            break
        ib = ((r[0] << 8) | r[1]) / 100
        iw = ((r[2] << 8) | r[3]) / 100
        T = (r[4] - 40) + r[5] / 100
        if iw > 655 and ib > 655 and T > 215:   # 结束哨兵
            break
        glu_mg = ((r[6] & 0x0F) << 8) + r[7]
        trend = r[6] >> 4
        err = r[8]
        recs.append((start_id + i, glu_mg, round(glu_mg / 18, 1), round(ib, 2), round(iw, 2), round(T, 2), trend, err))
    return recs


async def main():
    randB = derive(PHONE)
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: "anytime" in ((a.local_name or d.name or "").lower()), timeout=25.0)
    if not dev:
        print("没扫到设备(确认手机已断开)。"); return

    all_recs = {}
    done = asyncio.Event()
    pending = {"batch": None}

    async with BleakClient(dev, timeout=25.0) as client:
        print(f"已连接 {dev.address}，开始全历史抓取...")

        def handler(_c, data):
            b = bytes(data)
            if b and b[0] == 0x37:
                pending["batch"] = b

        await client.start_notify(NOTIFY, handler)
        await client.write_gatt_char(WRITE, frame(0x31, randB), response=False)
        await asyncio.sleep(1.2)
        n = datetime.datetime.now()
        await client.write_gatt_char(WRITE, frame(0x03, [n.year - 1900, n.month, n.day, n.hour, n.minute, n.second]), response=False)
        await asyncio.sleep(1.2)

        start = 1
        full_size = None
        for _ in range(300):              # 上限保护
            pending["batch"] = None
            fp = frame(0x37, [start & 0xFF, (start >> 8) & 0xFF, COUNT])
            await client.write_gatt_char(WRITE, fp, response=False)
            for _w in range(30):
                if pending["batch"] is not None:
                    break
                await asyncio.sleep(0.1)
            b = pending["batch"]
            if b is None:
                print(f"  id={start} 无响应,停止"); break
            recs = parse_batch(b, CIPHER)
            if not recs:
                print(f"  id={start} 返回空,拉完"); break
            for r in recs:
                all_recs[r[0]] = r
            last_id = recs[-1][0]
            print(f"  id {recs[0][0]}..{last_id}  共{len(recs)}条  最新血糖≈{recs[-1][2]}mmol/L")
            if full_size is None:
                full_size = len(recs)
            if len(recs) < full_size:     # 不足整批 = 最后一批
                break
            start = last_id + 1

        # 写 CSV，按 id 升序，时间用 interval 反推
        ids = sorted(all_recs)
        if not ids:
            print("没拉到数据。"); return
        max_id = ids[-1]
        now = datetime.datetime.now()
        with open(CSV_PATH, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "glucoseId", "glu_mmol", "glu_mg", "Ib", "Iw", "T", "trend"])
            for gid in ids:
                r = all_recs[gid]
                if r[1] <= 0:             # 仅跳过预热期 glu=0（空槽已在解析时停止，无需过滤）
                    continue
                ts = now - datetime.timedelta(seconds=(max_id - gid) * INTERVAL)
                cal_mmol = round(r[1] * CALIB / 18, 1)   # 标定后 mmol(对齐App)
                cal_mg = round(r[1] * CALIB)
                w.writerow([ts.isoformat(timespec="seconds"), gid, cal_mmol, cal_mg, r[3], r[4], r[5], r[6]])
        valid = [all_recs[g] for g in ids if 36 <= all_recs[g][1] <= 450]
        print(f"\n完成: 共{len(ids)}条(含预热),有效血糖{len(valid)}条 → {CSV_PATH}")
        if valid:
            gl = [v[2] for v in valid]
            print(f"血糖范围 {min(gl)}–{max(gl)} mmol/L,最新 {valid[-1][2]} mmol/L (id={valid[-1][0]})")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"出错: {e!r}")
