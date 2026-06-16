"""解析 Android 的 btsnoop_hci.log，提取所有 BLE ATT 写入/通知操作。
目的: 找出官方 App 连上鱼跃 CGM 后，往命令特征写了什么、血糖从哪个 handle 推回来。
无需第三方库。用法: python parse_btsnoop.py [btsnoop_hci.log]
"""
import struct
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "btsnoop_hci.log"

# ATT 操作码
ATT_OPS = {
    0x0A: "READ_REQ", 0x0B: "READ_RSP",
    0x12: "WRITE_REQ", 0x13: "WRITE_RSP",
    0x52: "WRITE_CMD",
    0x1B: "NOTIFY", 0x1D: "INDICATE", 0x1E: "INDICATE_CFM",
    0x16: "PREP_WRITE_REQ", 0x18: "EXEC_WRITE_REQ",
}


def read_btsnoop(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"btsnoop\x00":
        print("不是标准 btsnoop 文件头，尝试继续...")
    off = 16  # 跳过文件头(8 magic + 4 version + 4 datalink)
    recs = []
    while off + 24 <= len(data):
        olen, ilen, flags, drops, ts = struct.unpack(">IIIIq", data[off:off + 24])
        off += 24
        pkt = data[off:off + ilen]
        off += ilen
        recs.append((flags, ts, pkt))
    return recs


def parse_acl_att(pkt):
    # HCI ACL: 第1字节 packet type(02=ACL)。Android btsnoop 通常带 type 前缀。
    if not pkt:
        return None
    p = pkt
    if p[0] == 0x02:  # HCI ACL Data
        p = p[1:]
    if len(p) < 8:
        return None
    # ACL header: handle(2) + total_len(2)
    # L2CAP: length(2) + cid(2)
    cid = struct.unpack("<H", p[6:8])[0]
    if cid != 0x0004:  # ATT 固定 CID
        return None
    att = p[8:]
    if not att:
        return None
    op = att[0]
    if op not in ATT_OPS:
        return None
    handle = None
    value = b""
    if op in (0x12, 0x52, 0x1B, 0x1D, 0x0B):
        if op == 0x0B:  # read rsp 无 handle
            value = att[1:]
        else:
            if len(att) >= 3:
                handle = struct.unpack("<H", att[1:3])[0]
                value = att[3:]
    return ATT_OPS[op], handle, value


def main():
    try:
        recs = read_btsnoop(PATH)
    except FileNotFoundError:
        print(f"找不到 {PATH}。把手机导出的 btsnoop_hci.log 放到这个目录，或传路径参数。")
        return
    print(f"共 {len(recs)} 条 HCI 记录。提取 ATT 操作:\n")
    n = 0
    for flags, ts, pkt in recs:
        r = parse_acl_att(pkt)
        if not r:
            continue
        op, handle, value = r
        # 只关心带数据的写入和通知
        if op in ("WRITE_REQ", "WRITE_CMD", "NOTIFY", "INDICATE"):
            direction = "↑发" if op.startswith("WRITE") else "↓收"
            hx = value.hex(" ")
            hs = f"0x{handle:04x}" if handle is not None else "—"
            print(f"{direction} {op:10} handle={hs}  {len(value):>3}B  {hx}")
            n += 1
    print(f"\n共 {n} 条写入/通知。重点看: App 连上后最早的几条 ↑发(激活命令)，"
          f"以及随后周期性的 ↓收(血糖数据)。把它们贴给我即可。")


if __name__ == "__main__":
    main()
