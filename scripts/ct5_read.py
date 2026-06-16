"""鱼跃 CGM CT5 实时血糖读取 (逆向复现，非破坏性)。
协议见 CT5_protocol.md。只发只读类命令(checkId/校时/拉历史/推送ACK)，不发 setId 重绑。
用法: python ct5_read.py
确保手机官方 App / 蓝牙已断开。
"""
import asyncio
import datetime
from bleak import BleakClient, BleakScanner

SVC   = "00001000-1212-efde-1523-785feabcd123"
WRITE = "00001002-1212-efde-1523-785feabcd123"
NOTIFY= "00001001-1212-efde-1523-785feabcd123"

RANDOM_B = [1, 1, 1, 1]   # 默认通信ID "111111111111" 派生


def receive_sum(b):
    return sum(b) & 0xFF


def frame(cmd, payload=b""):
    body = bytes([cmd]) + bytes(payload)
    return body + bytes([receive_sum(body)])


def encode(data, cipher):
    """ConvertTools.encode = 解密方向: 先 XOR cipher，再相邻位去扰。"""
    bits = "".join(f"{(x ^ cipher) & 0xFF:08b}" for x in data)
    bl = list(bits)
    for i in range(len(bl) - 1):
        if bl[i + 1] == "0":
            bl[i] = "1" if bl[i] == "0" else "0"
    bits = "".join(bl)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def parse_with_cipher(fr, cipher):
    """对一帧(含cmd/id/校验)用给定cipher解密并解析。返回 dict 或 None(不合理)。"""
    if len(fr) < 12:
        return None
    plain = encode(fr[3:-1], cipher)
    buf = bytes(fr[:3]) + plain
    if len(buf) < 11:
        return None
    gid = buf[1] + (buf[2] << 8)
    ib = ((buf[3] << 8) | buf[4]) / 100
    iw = ((buf[5] << 8) | buf[6]) / 100
    T = (buf[7] - 40) + buf[8] / 100
    glu_mg = ((buf[9] & 0x0F) << 8) + buf[10]
    glu_mmol = round(glu_mg / 18, 1)
    # 合理性过滤
    if not (0 <= ib < 655 and 0 <= iw < 655 and 20 <= T <= 45 and 36 <= glu_mg <= 4000):
        return None
    return dict(gid=gid, ib=round(ib, 2), iw=round(iw, 2), T=round(T, 2),
                glu_mg=glu_mg, glu_mmol=glu_mmol, cipher=cipher)


def brute_force(fr):
    """穷举 cipher 0..255，返回所有数值合理的候选。"""
    hits = []
    for c in range(256):
        r = parse_with_cipher(fr, c)
        if r:
            hits.append(r)
    return hits


KNOWN_CIPHER = None  # 一旦锁定就复用


async def main():
    print("扫描 Anytime 设备(确保手机已断开)...")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: "anytime" in ((a.local_name or d.name or "").lower()), timeout=25.0)
    if not dev:
        print("没扫到。确认手机 App/蓝牙已断开。")
        return
    print(f"连接 {dev.address} {dev.name} ...")

    glucose_cmds = {0x07, 0x36, 0x37}

    async with BleakClient(dev, timeout=25.0) as client:
        print(f"已连接 MTU={client.mtu_size}")

        def handler(_c, data):
            global KNOWN_CIPHER
            b = bytes(data)
            cmd = b[0] if b else -1
            print(f"\n← notify {len(b)}B cmd=0x{cmd:02x}: {b.hex(' ')}")
            ok = (b[-1] == receive_sum(b[:-1])) if len(b) >= 2 else False
            print(f"   校验{'通过' if ok else '不符'}")
            if cmd in glucose_cmds:
                if KNOWN_CIPHER is not None:
                    r = parse_with_cipher(b, KNOWN_CIPHER)
                    if r:
                        print(f"   ★血糖: {r['glu_mmol']} mmol/L ({r['glu_mg']} mg/dL) "
                              f"id={r['gid']} Iw={r['iw']} Ib={r['ib']} T={r['T']}℃ [cipher={KNOWN_CIPHER}]")
                        return
                hits = brute_force(b)
                if hits:
                    print(f"   穷举命中 {len(hits)} 个候选 cipher:")
                    for r in hits[:8]:
                        print(f"     cipher={r['cipher']:3d} → {r['glu_mmol']} mmol/L "
                              f"({r['glu_mg']} mg/dL) id={r['gid']} Iw={r['iw']} Ib={r['ib']} T={r['T']}℃")
                    if len(hits) == 1:
                        KNOWN_CIPHER = hits[0]['cipher']
                        print(f"   → 唯一候选，锁定 cipher={KNOWN_CIPHER}")
                else:
                    print("   穷举无合理候选(可能不是血糖帧/解析方向需调整)")
            # 推送帧回 ACK，维持设备持续上报
            if cmd == 0x07:
                asyncio.create_task(ack(client))

        async def ack(cl):
            try:
                await cl.write_gatt_char(WRITE, bytes([0x35, 0x55, 0xAA, 0x34]), response=False)
            except Exception as e:
                print(f"   (ACK 失败 {e})")

        await client.start_notify(NOTIFY, handler)
        print("已订阅 notify 1001。开始握手序列...")

        async def w(name, data, wait=1.5):
            print(f"→ {name}: {bytes(data).hex(' ')}")
            await client.write_gatt_char(WRITE, bytes(data), response=False)
            await asyncio.sleep(wait)

        # 1) checkId (只读校验，RandomB 默认)
        await w("checkId(0x31)", frame(0x31, RANDOM_B))
        # 2) 校时 setDate(0x03)
        n = datetime.datetime.now()
        await w("setDate(0x03)", frame(0x03, [n.year - 1900, n.month, n.day, n.hour, n.minute, n.second]))
        # 3) init / 设备名 (App 流程里有)
        await w("deviceName(0x3B)", bytes([0x3B, 0x55, 0xAA, 0x3A]))
        await w("SSN(0x3F)", bytes([0x3F, 0x55, 0xAA, 0x3E]))
        # 4) 拉一批历史(从 id=1)，用于触发数据 & 穷举 cipher
        await w("fastpull(0x37) id=1 count=45", frame(0x37, [1, 0, 45]))

        print("\n=== 监听 240 秒，等待推送/历史数据(CGM 每3分钟一个点) ===")
        await asyncio.sleep(240)
        print("\n监听结束。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"出错: {e!r}")
