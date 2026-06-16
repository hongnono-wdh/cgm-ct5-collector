"""离线从 raw_batches.txt 重建 glucose_log.csv，应用标定系数对齐 App。不连设备。
用法: python rebuild_csv.py [CALIB,默认1.327] [interval秒,默认180]
"""
import sys
import csv
import datetime

CIPHER = 124
CALIB = float(sys.argv[1]) if len(sys.argv) > 1 else 1.327
INTERVAL = int(sys.argv[2]) if len(sys.argv) > 2 else 180


def encode(data, c):
    bits = "".join(f"{(x ^ c) & 0xFF:08b}" for x in data)
    bl = list(bits)
    for i in range(len(bl) - 1):
        if bl[i + 1] == "0":
            bl[i] = "1" if bl[i] == "0" else "0"
    return bytes(int("".join(bl)[i:i + 8], 2) for i in range(0, len(bl), 8))


def rsum(b):
    return sum(b) & 0xFF


def parse_batch(fr):
    if fr[0] != 0x37 or fr[-1] != rsum(fr[:-1]):
        return []
    start_id = fr[1] + (fr[2] << 8)
    payload = encode(bytes(fr[3:-1]), CIPHER)
    out = []
    for i in range(len(payload) // 15):
        r = payload[i * 15:i * 15 + 9]
        if len(r) < 9 or all(x == 0x7b for x in r):
            break
        ib = ((r[0] << 8) | r[1]) / 100
        iw = ((r[2] << 8) | r[3]) / 100
        T = (r[4] - 40) + r[5] / 100
        glu_mg = ((r[6] & 0x0F) << 8) + r[7]
        out.append((start_id + i, glu_mg, round(ib, 2), round(iw, 2), round(T, 2), r[6] >> 4))
    return out


def main():
    recs = {}
    with open("raw_batches.txt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            _, hexs = line.split("\t")
            for r in parse_batch(bytes.fromhex(hexs)):
                recs[r[0]] = r
    ids = sorted(recs)
    valid = [i for i in ids if recs[i][1] > 0]
    if not valid:
        print("无有效数据"); return
    max_id = max(valid)
    now = datetime.datetime.now()
    with open("glucose_log.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "glucoseId", "glu_mmol", "glu_mg", "Ib", "Iw", "T", "trend"])
        for gid in valid:
            r = recs[gid]
            ts = now - datetime.timedelta(seconds=(max_id - gid) * INTERVAL)
            cal_mmol = round(r[1] * CALIB / 18, 1)
            cal_mg = round(r[1] * CALIB)
            w.writerow([ts.isoformat(timespec="seconds"), gid, cal_mmol, cal_mg, r[2], r[3], r[4], r[5]])
    gl = [round(recs[i][1] * CALIB / 18, 1) for i in valid]
    print(f"重建完成: {len(valid)} 个有效点(id {valid[0]}..{valid[-1]}), 标定系数×{CALIB}")
    print(f"标定后血糖范围 {min(gl)}–{max(gl)} mmol/L, 最新 {gl[-1]} mmol/L (id={valid[-1]})")


if __name__ == "__main__":
    main()
