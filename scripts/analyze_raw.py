"""离线分析 raw_batches.txt：对指定 startId 的原始帧，按 15 字节/记录逐条解码打印。
用法: python analyze_raw.py [startId,默认133]
"""
import sys

CIPHER = 124
WANT = int(sys.argv[1]) if len(sys.argv) > 1 else 133


def encode(data, c):
    bits = "".join(f"{(x ^ c) & 0xFF:08b}" for x in data)
    bl = list(bits)
    for i in range(len(bl) - 1):
        if bl[i + 1] == "0":
            bl[i] = "1" if bl[i] == "0" else "0"
    return bytes(int("".join(bl)[i:i + 8], 2) for i in range(0, len(bl), 8))


def main():
    frames = {}
    with open("raw_batches.txt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            sid, hexs = line.split("\t")
            frames[int(sid)] = bytes.fromhex(hexs)
    if WANT not in frames:
        print("可用 startId:", sorted(frames)[:10], "..."); return
    fr = frames[WANT]
    start_id = fr[1] + (fr[2] << 8)
    print(f"startId={WANT} 帧startId={start_id} 帧长={len(fr)} 校验字节={fr[-1]:02x}")
    payload = encode(fr[3:-1], CIPHER)
    print(f"解密payload长度={len(payload)}, 可分 {len(payload)//15} 条(15B) / {len(payload)//11} 条(11B)\n")
    print("idx  id   原始15字节                                    Ib     Iw     T     gluByte glu_mg mmol trend err  尾部(电压)")
    for i in range(len(payload) // 15):
        r = payload[i * 15:i * 15 + 15]
        ib = ((r[0] << 8) | r[1]) / 100
        iw = ((r[2] << 8) | r[3]) / 100
        T = (r[4] - 40) + r[5] / 100
        gb = r[6]
        glu_mg = ((gb & 0x0F) << 8) + r[7]
        trend = gb >> 4
        err = r[8]
        tail = r[9:].hex(" ")
        print(f"{i:3d} {start_id+i:4d}  {r.hex(' ')}  {ib:6.1f} {iw:6.2f} {T:6.2f}  0x{gb:02x}   {glu_mg:4d}  {glu_mg/18:4.1f}  {trend:2d}  0x{err:02x}  {tail}")


if __name__ == "__main__":
    main()
