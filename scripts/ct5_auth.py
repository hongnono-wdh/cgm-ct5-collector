"""鱼跃 CGM CT5 正规握手 + 实时血糖读取。
RandomA/B 由真实手机号派生 → setId 确定性算出 cipher → 解密 0x07 推送。
用法: python ct5_auth.py [监听秒数,默认90]
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
LISTEN = int(sys.argv[1]) if len(sys.argv) > 1 else 90


def derive_randoms(phone):
    s = phone
    if len(s) < 12:
        s = s + "0" * (12 - len(s))
    elif len(s) > 12:
        s = s[:12]
    if s.startswith("0"):
        s = "1" + s[1:]
    randA = [ord(c) - ord("0") for c in s[4:8]]
    randB = [ord(c) - ord("0") for c in s[8:12]]
    return s[:4], randA, randB


def receive_sum(b):
    return sum(b) & 0xFF


def convolve(a, b):
    out = [0] * len(a)
    for i in range(len(a)):
        for j in range(len(b)):
            if i - j >= 0:
                out[i] += a[j] * b[i - j]
    return out


def set_id_request(randA, randB):
    conv = convolve(randB, randA)              # convolve(iArr2=RandomB, iArr=RandomA)
    body = [0x30] + [x & 0xFF for x in randB] + [x & 0xFF for x in conv]  # 9 bytes (0..8)
    body = body[:9]
    return bytes(body + [receive_sum(body)])


def cipher_from_response(resp, randA):
    # subArray(resp,5) = resp[5:-1]
    seg = list(resp[5:-1])
    conv = convolve([x & 0xFF for x in seg], randA)
    acc = conv[0]
    for i in range(1, len(conv)):
        acc ^= conv[i]
    return acc & 0xFF


def check_id_request(randB):
    body = [0x31] + [x & 0xFF for x in randB]   # bytes 0..4
    return bytes(body + [receive_sum(body)])


def set_date_request():
    n = datetime.datetime.now()
    body = [0x03, n.year - 1900, n.month, n.day, n.hour, n.minute, n.second]
    return bytes(body + [receive_sum(body)])


def encode(data, cipher):
    bits = "".join(f"{(x ^ cipher) & 0xFF:08b}" for x in data)
    bl = list(bits)
    for i in range(len(bl) - 1):
        if bl[i + 1] == "0":
            bl[i] = "1" if bl[i] == "0" else "0"
    return bytes(int("".join(bl)[i:i + 8], 2) for i in range(0, len(bl), 8))


def parse_glucose(fr, cipher):
    plain = encode(fr[3:-1], cipher)
    buf = bytes(fr[:3]) + plain
    if len(buf) < 11:
        return None
    gid = buf[1] + (buf[2] << 8)
    ib = ((buf[3] << 8) | buf[4]) / 100
    iw = ((buf[5] << 8) | buf[6]) / 100
    T = (buf[7] - 40) + buf[8] / 100
    glu_mg = ((buf[9] & 0x0F) << 8) + buf[10]
    return dict(gid=gid, ib=round(ib, 2), iw=round(iw, 2), T=round(T, 2),
                glu_mg=glu_mg, glu_mmol=round(glu_mg / 18, 1),
                trend=buf[9] >> 4, raw=buf.hex(" "))


def log_csv(r):
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "glucoseId", "glu_mmol", "glu_mg", "Ib", "Iw", "T", "trend"])
        w.writerow([datetime.datetime.now().isoformat(timespec="seconds"),
                    r["gid"], r["glu_mmol"], r["glu_mg"], r["ib"], r["iw"], r["T"], r["trend"]])


async def main():
    prefix, randA, randB = derive_randoms(PHONE)
    print(f"手机号 {PHONE} → prefix={prefix} RandomA={randA} RandomB={randB}")
    print("扫描设备...")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: "anytime" in ((a.local_name or d.name or "").lower()), timeout=25.0)
    if not dev:
        print("没扫到。确认手机 App/蓝牙已断开。")
        return

    state = {"cipher": None, "frames": []}

    async with BleakClient(dev, timeout=25.0) as client:
        print(f"已连接 {dev.address} MTU={client.mtu_size}")

        def handler(_c, data):
            b = bytes(data)
            cmd = b[0] if b else -1
            ok = len(b) >= 2 and b[-1] == receive_sum(b[:-1])
            print(f"← {len(b)}B cmd=0x{cmd:02x} chk={'OK' if ok else 'X'}: {b.hex(' ')}")
            # setId 应答 → 算 cipher
            if cmd == 0x30 and ok and state["cipher"] is None:
                state["cipher"] = cipher_from_response(b, randA)
                print(f"  ★ 算出 cipher = {state['cipher']}")
            # checkId 应答
            elif cmd == 0x31:
                print(f"  checkId 结果 = {'成功(已认证)' if (len(b) > 5 and b[5] == 1) else '失败'}")
            # 疑似血糖帧(0x07 推送 / 0x36/0x37 / 也含我们见过的 0x35),长度>=15
            elif len(b) >= 15 and state["cipher"] is not None:
                r = parse_glucose(b, state["cipher"])
                if r:
                    sane = 20 <= r["glu_mg"] <= 450 and 20 <= r["T"] <= 45
                    tag = "★血糖" if sane else "  (解出但数值存疑)"
                    print(f"  {tag}: {r['glu_mmol']} mmol/L ({r['glu_mg']} mg/dL) "
                          f"id={r['gid']} Iw={r['iw']} Ib={r['ib']} T={r['T']}℃ trend={r['trend']}")
                    if sane:
                        log_csv(r)
                # 推送帧回 ACK
                if cmd == 0x07:
                    asyncio.create_task(client.write_gatt_char(WRITE, bytes([0x35, 0x55, 0xAA, 0x34]), response=False))

        await client.start_notify(NOTIFY, handler)
        print("已订阅 1001。开始握手:")

        async def w(name, data, wait=2.0):
            print(f"→ {name}: {data.hex(' ')}")
            await client.write_gatt_char(WRITE, data, response=False)
            await asyncio.sleep(wait)

        await w("checkId", check_id_request(randB))
        await w("setId", set_id_request(randA, randB))   # 拿 cipher
        await w("setDate", set_date_request())

        # 验证: 用算出的 cipher 解之前抓到的 19B 帧
        if state["cipher"] is not None:
            test = bytes.fromhex("358900d6d6d70b14e1e914d6b2b2b5f2d4c4b7")
            r = parse_glucose(test, state["cipher"])
            print(f"\n[验证] 用 cipher={state['cipher']} 解旧帧 → {r['glu_mmol']} mmol/L "
                  f"({r['glu_mg']} mg/dL) Iw={r['iw']} Ib={r['ib']} T={r['T']}℃\n")
        else:
            print("\n[!] 没拿到 cipher(setId 无应答?),后续无法解密。\n")

        print(f"=== 监听 {LISTEN} 秒,等待推送 (每~5分钟一帧) ===")
        await asyncio.sleep(LISTEN)
        print("监听结束。数据见", CSV_PATH)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"出错: {e!r}")
