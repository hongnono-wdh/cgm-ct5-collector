import asyncio, datetime
from bleak import BleakClient, BleakScanner
WRITE="00001002-1212-efde-1523-785feabcd123"; NOTIFY="00001001-1212-efde-1523-785feabcd123"
PHONE="13800138000"; CIPHER=124
def derive(p):
    s=p+"0"*(12-len(p)) if len(p)<12 else p[:12]
    s="1"+s[1:] if s.startswith("0") else s
    return [ord(c)-48 for c in s[8:12]]
def rsum(b): return sum(b)&0xFF
def frame(cmd,pl): body=[cmd]+list(pl); return bytes(body+[rsum(body)])
def encode(data,c):
    bits="".join(f"{(x^c)&0xFF:08b}" for x in data); bl=list(bits)
    for i in range(len(bl)-1):
        if bl[i+1]=="0": bl[i]="1" if bl[i]=="0" else "0"
    return bytes(int("".join(bl)[i:i+8],2) for i in range(0,len(bl),8))
async def main():
    randB=derive(PHONE)
    dev=await BleakScanner.find_device_by_filter(lambda d,a:"anytime" in ((a.local_name or d.name or "").lower()),timeout=20.0)
    if not dev: print("没扫到设备(可能被手机占用)"); return
    got={"ssn":None}
    async with BleakClient(dev,timeout=20.0) as c:
        print("已连接",dev.address)
        def h(_,data):
            b=bytes(data)
            if b and b[0]==0x3F:
                dec=encode(b[1:],CIPHER)
                ssn="".join(chr(x) for x in dec if 32<=x<127)
                print("0x3F响应原始:",b.hex(" "))
                print("解密:",dec.hex(" "))
                print("★ SSN(可打印部分):",ssn)
                got["ssn"]=ssn
        await c.start_notify(NOTIFY,h)
        await c.write_gatt_char(WRITE,frame(0x31,randB),response=False); await asyncio.sleep(1.2)
        n=datetime.datetime.now()
        await c.write_gatt_char(WRITE,frame(0x03,[n.year-1900,n.month,n.day,n.hour,n.minute,n.second]),response=False); await asyncio.sleep(1.2)
        await c.write_gatt_char(WRITE,bytes([0x3F,0x55,0xAA,0x3E]),response=False)
        await asyncio.sleep(3)
    print("完成" if got["ssn"] else "没拿到SSN")
asyncio.run(main())
