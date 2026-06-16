# 鱼跃 Yuwell CGM CT5 数据直读工具

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](LICENSE)
[![Non-Commercial](https://img.shields.io/badge/Use-%E4%BB%85%E5%AD%A6%E4%B9%A0%C2%B7%E7%A6%81%E5%95%86%E7%94%A8-red.svg)](DISCLAIMER.md)
[![Medical Disclaimer](https://img.shields.io/badge/%E2%9A%A0%EF%B8%8F-%E9%9D%9E%E5%8C%BB%E7%96%97%E7%94%A8%E9%80%94-orange.svg)](DISCLAIMER.md)

用 Python(蓝牙 BLE)绕开官方 App,直接读取**鱼跃第五代动态血糖仪 CT5** 的血糖数据,
解密、对齐 App 显示值,并提供一个本地监测网页面板。还附带把官方 native 标定算法
`decodeCT`(SSN→K/R)逆向出的纯 Python 复现。

> ⚠️ **仅供个人学习、研究与自有设备数据自取;不可商用;只能用于你本人合法拥有的设备与数据。**
> 涉及医疗数据,**请勿用于任何临床/用药决策**。使用前请完整阅读 [DISCLAIMER.md](DISCLAIMER.md)。

## ⚖️ 许可与免责(使用前必读)

- **许可协议**:本项目采用 [CC BY-NC 4.0](LICENSE)(署名-非商业性使用)。**禁止任何商业用途**,仅限学习/研究/非营利的互操作实践。
- **免责声明**:完整条款见 **[DISCLAIMER.md](DISCLAIMER.md)**,要点:
  - 🩺 **非医疗器械**,数据可能有误,**严禁用于任何医疗/用药/低血糖判断等健康决策**,请以官方设备并遵医嘱为准。
  - 💻 **只能操作你本人拥有的设备与数据**,**严禁**用于未经授权访问他人设备/账户/服务器或任何网络入侵行为;须遵守当地法律(如《网络安全法》《刑法》285/286 条等)。
  - 🔒 本仓库**不含任何原厂 App/二进制/固件**,仅为独立编写的 Python 代码与文字说明;商标与算法知识产权归各自权利人所有,本项目与原厂无任何关联或背书。
  - ⚠️ 非官方交互可能损坏传感器/发射器、使保修失效,**风险自负**。
- 脚本顶部的手机号(`PHONE`)为**占位符 `13800138000`**,需替换为你自己绑定设备的手机号才能使用。

---

## 〇、前因后果(这套东西怎么来的)

目标很简单:**我想拿到自己的血糖数据**,而不是只能在官方 App 里看。整个过程:

1. **想直接抓蓝牙包** → 手机是 Realme/ColorOS,系统把蓝牙 btsnoop 日志**加密成 `.cfa`**,抓包这条路被堵。
2. **改为反编译官方 App**(`com.yuwell.cgm` v3.8.17.1,用 jadx)→ 在 `com.yuwell.bluetooth.le.device.cgm` 与 `ist.com.sdk` 里找到完整 BLE 协议。
3. **摸清协议**:设备芯片 Renesas DA14535;数据服务 `00001000-1212-efde-1523-785feabcd123`,
   写命令→`...1002`,收数据←`...1001`;帧格式 `[命令][数据][8位求和校验]`,数据段加密。
4. **解决认证与解密**:认证用**手机号**派生的 RandomA/B(checkId 0x31);数据用单字节 `cipher`
   做 XOR+位扰加密——已绑定设备不响应 setId,所以 cipher 靠**抓几帧穷举 + 物理合理性筛选**得到(本机 = 124)。
5. **解析血糖**:每记录 15 字节,`GluMG=((byte6&0x0F)<<8)+byte7` mg/dL,`mmol=GluMG/18`;
   并能用 fastpull(0x37)**一次拉出传感器里存的全部历史**(几千点)。
6. **对齐 App 显示值**:发现 App 显示值由 native 库 `algorithm()` 重算。用官方 App 导出的
   Excel 逐点拟合,得 **App ≈ 原始×1.327**(r=0.993,稳态下就是个标定缩放,疑似传感器 K 值)。
7. **逆向标定来源 `decodeCT`**:进一步把 native 的 `decodeCT(SSN)→K/R` 静态逆向成纯 Python
   (`decodeCT.py`)——它其实是把传感器序列号 SSN 按位置切出 K/R 等字段,无需 .so/Frida/root。

详细技术文档见 `docs/`(协议、标定分析、decodeCT 逆向、可行性评估)。

---

## 一、目录结构与文件用途

```
yuwell-cgm/
├── README.md                  ← 本文件(先读这个)
├── requirements.txt           ← Python 依赖
├── dashboard.html             ← 本地监测网页面板(企业SaaS风格)
├── glucose_log.csv            ← 血糖数据(脚本生成/网页读取;含样例)
├── raw_batches.txt            ← 原始批量帧(离线重解析用;含样例)
├── scripts/
│   ├── ct5_setup.py           ← 【换传感器先跑】初始化:验证绑定 + 自动找 cipher
│   ├── ct5_fetch_all.py       ← 【日常主力】拉传感器全历史,标定后写 CSV
│   ├── ct5_logger.py          ← 实时长采集(边收边写 CSV)
│   ├── fit_calib.py           ← 用 App 导出的 Excel 拟合标定系数 CALIB
│   ├── decodeCT.py            ← 由 SSN 算 K/R(逆向 native 复现,可独立 import)
│   ├── read_ssn.py            ← 从设备读当前传感器 SSN
│   ├── rebuild_csv.py         ← 离线从 raw_batches.txt 重建 CSV(不连设备)
│   ├── ct5_capture_raw.py     ← 抓原始 0x37 批量帧存盘(调试)
│   ├── analyze_raw.py         ← 离线逐条解码某批(调试解析)
│   ├── ct5_auth.py / ct5_read.py ← 早期握手/穷举脚本(调试参考)
│   └── parse_btsnoop.py       ← 解析安卓 btsnoop 抓包(本机加密未用上)
├── docs/
│   ├── CT5_protocol.md            ← 完整 BLE 协议规格
│   ├── calibration_analysis.md    ← App显示值=原始×native算法 的分析
│   ├── decodeCT_reverse.md        ← decodeCT 逆向全过程(公式/系数/反汇编)
│   └── decodeCT_feasibility.md    ← 自取 K/R 的可行性评估
└── sample_data/               ← 一份样例 CSV 与原始帧备份
```

---

## 二、环境准备(在新机器上)

1. 装 **Python 3.9+**。
2. 装依赖:
   ```bash
   pip install -r requirements.txt
   ```
3. 这台机器要有**蓝牙**(BLE 脚本要连设备)。Windows 自带蓝牙即可(bleak 走 WinRT);
   Linux 需 BlueZ;macOS 原生支持。
4. **纯离线分析**(rebuild_csv / decodeCT / fit_calib / 看网页)不需要蓝牙。

---

## 三、必须按你的设备核对的 3 个参数 ⭐

脚本顶部有三个常量,**同步到新机器后请核对**(它们跟人/传感器绑定):

| 常量 | 当前值 | 含义 | 何时要改 |
|---|---|---|---|
| `PHONE` | `13800138000` | 绑定设备的手机号(派生认证 RandomA/B) | 换绑定手机号 |
| `CIPHER` | `124` | 数据解密密钥(单字节) | **换发射器**几乎必变;换传感器一般不变。用 `ct5_setup.py` 重测 |
| `CALIB` | `1.327` | 标定系数,App显示≈原始×CALIB | **每支传感器都不同**,换传感器用 `fit_calib.py` 重算 |

> 这些值写在 `ct5_fetch_all.py` / `ct5_logger.py` / `ct5_setup.py` 顶部。

---

## 四、日常使用(传感器已激活)

> 在**项目根目录**运行,数据文件会写到这里;`dashboard.html` 也在这里读 `glucose_log.csv`。

```bash
cd /path/to/yuwell-cgm

# 1) 拉全历史(标定后写 glucose_log.csv)。手机App需先断开,设备一次只接受一个连接。
python scripts/ct5_fetch_all.py

# 2) 起网页面板(另开终端,保持运行)
python -m http.server 8000
#    浏览器打开 http://127.0.0.1:8000/dashboard.html

# 3) 实时持续采集(可选;会占用蓝牙,期间手机连不上)
python scripts/ct5_logger.py
```

只想看现成数据 / 没蓝牙时:`python scripts/rebuild_csv.py 1.327` 从 `raw_batches.txt` 离线重建。

---

## 五、换新传感器的完整流程

> 传感器是耗材(14/16 天),发射器可重复用 ~2 年。

1. **官方 App 激活新传感器**(这步绕不开):贴上 → App 扫码/绑定 → 等 ~45 分钟预热。
   - 预热是传感器电化学探针在体内稳定的物理过程,跳不过;激活还要 App 把标定下发设备、服务器注册。
2. **关掉手机 App / 手机蓝牙。**
3. **跑初始化,确认 cipher**:
   ```bash
   python scripts/ct5_setup.py
   ```
   若 cipher 不是 124,把新值填进 `ct5_fetch_all.py` / `ct5_logger.py` 顶部的 `CIPHER`。
4. **重标定**(每支必做):
   - 用 CALIB=1.0 拉原始值:`python scripts/rebuild_csv.py 1.0`(或先 `ct5_fetch_all` 抓一份);
   - 等 App 出 10+ 个点后,在 App 里**导出血糖数据 Excel**;
   - 拟合:`python scripts/fit_calib.py 导出的.xlsx glucose_log.csv` → 得到新 `CALIB`,填回脚本。
5. 之后照"日常使用"采集即可。

---

## 六、关于 K/R 与"完全脱离 App 自激活"(进阶,选读)

- `decodeCT.py` 已能由 SSN 算出该传感器的 K/R(逆向自官方 native 库,纯 Python)。
  - 读当前 SSN:`python scripts/read_ssn.py`,再 `python scripts/decodeCT.py`(或 import `decode_ct`)。
- **但拿到 K/R ≠ 能自激活**:完整自激活还要 setId/setConfig/init,且可能涉及鱼跃服务器注册,
  **搞砸会废掉一支传感器**。目前**建议仍用官方 App 激活**(一次性,顺带等预热),之后全程 PC 采集。
- 详见 `docs/decodeCT_feasibility.md` 与 `docs/decodeCT_reverse.md`。

---

## 七、踩坑经验 / 重要事实

- **设备同一时间只接受一个主机连接**:PC 采集时手机 App 连不上,反之亦然。
- 设备名按 `Anytime` 前缀扫描,换发射器自动适配,不用记 MAC。
- 批量历史(0x37):设备对任意 startId 都返回满 499B;**解密后整条全是 `0x7b` 的记录=空槽**,
  遇到即真实数据边界,停止(别按 11 字节做 FC/FF 清理,会破坏 15 字节对齐)。
- 历史数据不带时间戳,脚本按 3 分钟/点反推(CT5 标称采样间隔),横轴时间为近似值。
- App 在血糖**剧烈变化**时算法会做超前补偿,简单缩放(×CALIB)会差到 ~1 mmol/L;稳态下误差通常 ≤0.3。
- 本机抓包受阻是因为 ColorOS 加密 btsnoop,所以协议是靠**反编译**得到——换别的手机如果能抓包会更省事。
```
