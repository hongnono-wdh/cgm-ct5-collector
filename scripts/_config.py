"""设备 / 隐私相关参数加载器 —— 不在代码里硬编码隐私信息。

读取优先级(从高到低):
  1. 环境变量  CT5_PHONE / CT5_CIPHER / CT5_CALIB
  2. 项目根目录的 config.local.json (已被 .gitignore 忽略,请勿提交)
  3. 占位默认值(仅为让代码可导入/演示,无法连真机)

config.local.json 示例:
{
  "phone": "1xxxxxxxxxx",   // 你绑定设备的真实手机号(认证派生用)
  "cipher": 124,            // 数据解密密钥(单字节;换发射器多半要变)
  "calib": 1.327            // 标定系数(每支传感器不同)
}
"""
import os
import json
import pathlib
import datetime

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_LOCAL = _ROOT / "config.local.json"
_cfg = {}
if _LOCAL.exists():
    try:
        _cfg = json.loads(_LOCAL.read_text(encoding="utf-8"))
    except Exception as e:  # 配置坏了不致命,回退默认并提示
        print(f"[_config] 读取 {_LOCAL.name} 失败,使用默认值: {e!r}")


def get(key, default):
    """环境变量 CT5_<KEY> > config.local.json[key] > default。"""
    v = os.environ.get("CT5_" + key.upper())
    if v is None:
        v = _cfg.get(key)
    return default if v is None else v


PHONE = str(get("phone", "13800138000"))
CIPHER = int(get("cipher", 124))
CALIB = float(get("calib", 1.327))

# 时区:CGM 数据本身不带时间戳,由脚本打时间。固定按配置时区(默认北京 UTC+8),
# 这样无论运行机器在哪个时区,写出的时间都是中国时间。可用 CT5_TZ_OFFSET / tz_offset 调整。
TZ_OFFSET = float(get("tz_offset", 8))
TZ = datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))


def now():
    """配置时区(默认北京)的当前时间,返回 naive datetime(不带 tzinfo,保持 CSV 时间格式一致)。"""
    return datetime.datetime.now(TZ).replace(tzinfo=None)

if PHONE == "13800138000":
    print("[_config] ⚠️ 正在使用占位手机号,无法通过设备认证。"
          "请设置环境变量 CT5_PHONE 或在 config.local.json 填入真实绑定手机号。")
