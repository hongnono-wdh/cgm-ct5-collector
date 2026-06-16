"""拟合标定系数: 用官方 App 导出的 Excel(含 glucoseId/时间/血糖值) 对比本工具的原始 GluMG,
算出 该传感器的 CALIB(App显示值 ≈ 原始mmol × CALIB)。换传感器后跑一次。
用法: python fit_calib.py <app导出xlsx路径> [raw_csv,默认 glucose_log_raw.csv]
说明: 需要一份"未标定"的原始 CSV(用 CALIB=1.0 跑 ct5_fetch_all 生成,或用 rebuild_csv.py 1.0)。
"""
import sys
import csv

try:
    import openpyxl
except ImportError:
    print("缺 openpyxl: pip install openpyxl"); sys.exit(1)

if len(sys.argv) < 2:
    print(__doc__); sys.exit(0)
XLSX = sys.argv[1]
RAW = sys.argv[2] if len(sys.argv) > 2 else "glucose_log_raw.csv"

# App 导出: 列 = glucoseId, 时间, 血糖值
ws = openpyxl.load_workbook(XLSX, read_only=True, data_only=True).worksheets[0]
app = {}
for r in list(ws.iter_rows(values_only=True))[1:]:
    try:
        app[int(r[0])] = float(r[2])
    except Exception:
        pass

# 原始 CSV (CALIB=1.0 跑出来的): glucoseId, glu_mmol(=原始)
mine = {}
with open(RAW) as f:
    for row in csv.DictReader(f):
        mine[int(row["glucoseId"])] = float(row["glu_mmol"])

common = sorted(set(app) & set(mine))
if len(common) < 5:
    print(f"重叠点太少({len(common)})。确认 RAW 是用 CALIB=1.0 生成的原始值,且与 App 同一传感器。"); sys.exit(1)

# 过原点线性拟合 App = k * raw
sxx = sum(mine[i] ** 2 for i in common)
sxy = sum(mine[i] * app[i] for i in common)
k = sxy / sxx
res = [app[i] - k * mine[i] for i in common]
rms = (sum(e * e for e in res) / len(res)) ** 0.5
print(f"重叠 {len(common)} 点, id {common[0]}..{common[-1]}")
print(f"★ 标定系数 CALIB = {k:.4f}  (残差均方根 {rms:.3f} mmol/L)")
print(f"   把它填进 ct5_fetch_all.py / ct5_logger.py 的 CALIB = {k:.3f}")
print("\n抽查:")
print("id  | App | 原始×CALIB | 原始")
for i in common[::max(1, len(common) // 10)]:
    print(f"{i:4d}| {app[i]:4.1f}| {k*mine[i]:6.1f} | {mine[i]:.1f}")
