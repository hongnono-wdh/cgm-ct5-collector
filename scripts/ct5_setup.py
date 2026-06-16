"""鱼跃 CT5 新传感器/新发射器 一键初始化。
前提: 已用官方 App 激活传感器并过了预热(45分钟),且手机 App 已断开。
作用: 验证手机号绑定 → fastpull 一批历史 → 自动穷举 cipher(物理合理性打分) → 输出 cipher。
之后把得到的 cipher 填进 ct5_fetch_all.py / ct5_logger.py 的 CIPHER。
用法: python ct5_setup.py [手机号,默认沿用脚本内PHONE]
"""
import asyncio
import sys
import datetime
from bleak import BleakClient, BleakScanner

WRITE = "00001002-1212-efde-1523-785feabcd123"
NOTIFY = "00001001-1212-efde-1523-785feabcd123"
PHONE = sys.argv[1] if len(sys.argv) > 1 else "13800138000"


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


def score_cipher(fr, cipher):
    """对一个 0x37 批量帧用某 cipher 解码，返回(合理记录数, 样例值)。"""
    payload = encode(bytes(fr[3:-1]), cipher)
    sane, last_glu, run = 0, None, 0
    sample = []
    for i in range(len(payload) // 15):
        r = payload[i * 15:i * 15 + 9]
        if len(r) < 9:
            break
        ib = ((r[0] << 8) | r[1]) / 100
        iw = ((r[2] << 8) | r[3]) / 100
        T = (r[4] - 40) + r[5] / 100
        glu = ((r[6] & 0x0F) << 8) + r[7]
        ok = (36 <= glu <= 450 and 26 <= T <= 42 and 0 <= iw <= 60 and 0 <= ib <= 60)
        cont = (last_glu is None) or abs(glu - last_glu) < 50
        if ok and cont:
            run += 1
            if last_glu is not None:
                last_glu = glu
            if len(sample) < 3:
                sample.append((round(glu / 18, 1), round(iw, 2), round(T, 1)))
        if ok:
            sane += 1
            last_glu = glu
    return sane, run, sample


async def main():
    randB = derive(PHONE)
    print(f"手机号 {PHONE} → RandomB={randB}")
    print("扫描设备(确保手机App已断开)...")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: "anytime" in ((a.local_name or d.name or "").lower()), timeout=25.0)
    if not dev:
        print("没扫到设备。"); return
    pending = {"b": None}
    async with BleakClient(dev, timeout=25.0) as client:
        print(f"已连接 {dev.address} {dev.name}")

        def handler(_c, data):
            b = bytes(data)
            if b and b[0] == 0x31:
                print(f"checkId: {'✔ 手机号绑定正确,已认证' if (len(b) > 5 and b[5] == 1) else '�’ 失败——手机号可能不对,或设备绑了别的号'}")
            if b and b[0] == 0x37:
                pending["b"] = b

        await client.start_notify(NOTIFY, handler)
        await client.write_gatt_char(WRITE, frame(0x31, randB), response=False)
        await asyncio.sleep(1.5)
        n = datetime.datetime.now()
        await client.write_gatt_char(WRITE, frame(0x03, [n.year - 1900, n.month, n.day, n.hour, n.minute, n.second]), response=False)
        await asyncio.sleep(1.5)
        # 拉一批历史用于穷举
        await client.write_gatt_char(WRITE, frame(0x37, [1, 0, 45]), response=False)
        for _ in range(40):
            if pending["b"] is not None:
                break
            await asyncio.sleep(0.1)
        fr = pending["b"]
        if fr is None:
            print("fastpull 无响应,无法穷举 cipher。"); return

        print("\n穷举 cipher 0..255(按物理合理记录数打分)...")
        results = []
        for c in range(256):
            sane, run, sample = score_cipher(fr, c)
            if sane >= 5:
                results.append((sane, run, c, sample))
        results.sort(reverse=True)
        if not results:
            print("没找到合理 cipher——可能预热未完成(还没真实血糖),过会儿再试。"); return
        print("候选(合理记录数 最长连续 cipher 样例):")
        for sane, run, c, sample in results[:6]:
            print(f"  cipher={c:3d}  合理{sane}条 连续{run}  样例mmol/Iw/T={sample}")
        best = results[0][2]
        print(f"\n★ 最可能 cipher = {best}")
        print(f"   把它填进 ct5_fetch_all.py / ct5_logger.py 顶部的 CIPHER = {best}")
        print("\n下一步标定: 等 App 出 ~10+ 个点后导出 App 数据(Excel),")
        print("   跑 fit_calib.py 自动拟合该传感器的标定系数,填进 CALIB。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"出错: {e!r}")
