# 鱼跃 (Yuwell) CGM CT5 蓝牙 BLE 协议规格 (逆向)

> 逆向自官方 App jadx 反编译产物 (`F:\code\report\jadx_out\sources\`)。
> 设备: 第五代动态血糖仪 CT5 / 芯片 Renesas DA14535 / 固件 V1130_20250618。
> 本文所有字节均为十进制或 0x 十六进制；混淆产物里负数 byte 已按无符号还原 (例如 `-86` == `0xAA`)。

---

## 0. 关键结论速览

- CT5 走的是 **AgreementD** (`CGMDeviceCT5.getAgreement()` 返回 `CGMAgreementD`，见 `CGMDeviceCT5.java:28-30`)。其余 sender/handler 几乎全是空壳，真正的字节构造/解析都在 `ist.com.sdk.ProtocolToolsHolder` (静态方法) 和 `ist.com.sdk.ConvertTools` (加解密)。
- GATT：服务 `00001000-…`，写 `00001002-…` (write, App→设备)，notify `00001001-…` (设备→App)。代码里写特征用的是 `00001002`，notify 订阅 `00001001`，与你实测一致 (`EGattMessage.CT5`，`EGattMessage.java:12`)。
- **统一帧格式**：`[cmd(1B)] [payload …] [checksum(1B)]`。checksum = payload+cmd 的字节算术和的低 8 位 (`ReceiveSum`)。**没有长度字段，没有 CRC，只有 8-bit 求和校验**。命令帧第 1 字节是命令码，应答帧第 1 字节回显同一命令码 (用作分发，见 `handleData`)。
- 很多简单命令是 **4 字节固定模板** `{cmd, 0x55, 0xAA, checksum}`，其中 checksum 恰好 = cmd-1 (因 0x55+0xAA=0xFF, 加 cmd 后低 8 位 = cmd-1)。
- **加密**：血糖数据 / SSN / K-R 参数的 payload 用一个 **单字节 `cipher` (0..255)** 做对称变换 (XOR + 相邻位翻转)，算法在 `ConvertTools.encode/decode`，**纯 Java、可完全复现，无 native 依赖**。
- `cipher` 的来源是 **setID 握手** (命令 0x30) 的应答：App 发随机数 A、B，设备回卷积结果，App 端 `setIDResponse_CT5` 算出 cipher。**这是复现的关键拦路虎**(见 §1)。
- 仅 K/R 传感器码解码 (`querySSN_response` → `AlgorithmTools.decodeCT`) 走 native 库 `libalgorithm_jni_1_29_0_0.so`。**拿实时血糖值本身不需要它**——血糖电流→血糖值的换算在本协议里是设备端算好直接下发的 (`GluMG` 字段)，App 的 native 只是另算 AI 值。

---

## 1. 鉴权握手 (连接后第一时间发生什么)

### 1.1 触发链
`CGMManager.initialize()` 订阅 notify→`enableNotifications` 成功后回调 `onDevicePrepared` (`CGMManager.java:148-171`)。
上层 `CGMCallbackHandlerCT5.onDevicePrepared` (`CGMCallbackHandlerCT5.java:702`) 启动握手。

构造函数 (`CGMCallbackHandlerCT5.java:90-124`) 先把"通信 ID"(手机号或 `DeviceCommunicateId`，补零到 12 位，首位 0 改 1) 拆成三段：
- `f30413e` = id 字符串前 4 位 (字符串，后面当 4 字节 ASCII 用)
- `f30414f` = **RandomA** = 第 5–8 位 → 每个字符 `c-'0'` 转成 0..9 的 int[4] (`A0()`，`:398`)
- `f30415g` = **RandomB** = 第 9–12 位 → int[4]

> 注意：RandomA/B 不是真随机，而是由这个 12 位 ID 派生的固定值。默认 ID = `"111111111111"` → 前段 "1111"，RandomA=[1,1,1,1]，RandomB=[1,1,1,1]。

### 1.2 握手状态机 (按时间顺序)

1. **onDevicePrepared** → 查本地是否已绑定 (`getTransmitterState`)：
   - 未绑定 (`!ret.success`) → 调 `getVersion()` → 发 **版本查询** `transmitterVersion_request` (命令 0x01)，走首次绑定流程 (会经过 setId/getSSN/setConfig，见下)。
   - 已绑定 → 直接构造 `AuthInfo`(id, RandomA, RandomB, cipher = 本地存的 `Transmitter.sureClose`)，`setAuthInfo()`，然后 `checkId(RandomB)` (`CGMCallbackHandlerCT5.java:229-241`)。

2. **setId(RandomA, RandomB)** → 发命令 **0x30** (见 `setIDRequest_CT5`，`ProtocolToolsHolder.java:1516`)。仅首次绑定 / checkId 失败时走。
   - 帧：`bArr[10]`，`[0]=0x30`，`[1..4]=RandomB(原样4字节)`，`[5..8]=convolve(RandomB, RandomA)` 的低字节，`[9]=checksum(sum bytes0..8)`。
   - `convolve(a,b)`：离散卷积，`out[i] = Σ_{j} a[j]*b[i-j]` (i-j>=0)，输出长度 = len(a)=4 (`ConvertTools.convolve:50`)。

3. 设备回 **0x30 应答** → `handleSetId` (`CGMDeviceHandlerAgreementD.java:308`) → `setIDResponse_CT5(bArr, RandomA)` (`ProtocolToolsHolder.java:1534`)：
   - 取应答的 `subArray(bArr,5)` (去掉前 5 字节、去掉末尾校验) → `convolve(那段, RandomA)` → 把卷积结果数组逐个 XOR 累加 (`acc = conv[0]; for i>0: acc ^= conv[i]`) → `& 0xFF` = **cipher**。
   - cipher 存进 `AuthInfo.setCipher` 并回调 `onSetIDResponse` (`CGMCallbackHandlerCT5.java:785`)。cipher<0 视为失败断开；否则 `getSSN()`。

4. **checkId(RandomB)** → 命令 **0x31** (`checkIDRequest_CT5`，`ProtocolToolsHolder.java:31`)：
   - 帧 6 字节：`[0]=0x31`，`[1..4]=RandomB(4字节)`，`[5]=checksum(sum 0..4)`。
   - 应答 `handleCheckId`→`checkIDResponse_CT5`：合法且 `bArr[0]==0x31` 且 `bArr[5]==1` → 成功 (`ProtocolToolsHolder.java:47`)。
   - 成功后 (`onCheckIDResponse`，`:629`)：要解绑就 `unbind()`，否则 `setDate()`。

5. **getSSN** → 命令 **0x3F** (`querySSNRequest_CT5` = `{0x3F,0x55,0xAA,0x3E}`，`:1412`)。应答 0x3F → `querySSNResponse_CT5` 用 cipher 解密出传感器码字符串，再 `AlgorithmTools.decodeCT` (native) 得 K/R。回调 `onKRRead`→`setConfig(K,R,id前4位)`。

6. **setConfig(K,R,cipher,id4)** → 命令 **0x38** (`setParametersRequest_CT5`，`:1553`)：把 K/R/固定字节/4 位 ID 用 `ConvertTools.decode(..,cipher)` 加密后下发。应答 0x38→`handleConfig`→成功则 `sendInit()`(命令 0x06)。

7. **setDate** → 命令 **0x03** (`setDateRequest_CT2_5`，CT5 复用，`:1488`)：写当前时间 (年-1900, 月, 日, 时, 分, 秒)。应答 0x03→`onDateWriteResponse`→ 校时成功后开始拉数据 (`requestNextGlucoseData`)。

> **小结**：连接后写到 `1002` 的第一帧，对**已绑定**设备是 `checkId` (0x31)；对**首次绑定**设备是 `transmitterVersion` (0x01) 然后 setId(0x30)。两条路最终都要拿到 **cipher** 才能解密血糖数据。

### 1.3 你复现时的现实难点
- 若设备**已和你这台手机绑定过**：cipher 早就在 App 数据库 (`Transmitter.sureClose`)，重连只 `checkId`，不再重算。你自己用 bleak 连，需要先跑一遍 setId(0x30) 握手把 cipher 算出来 (纯 Java 逻辑，可用 Python 重写 `convolve`+XOR)。
- setId 的 RandomA/RandomB 由"通信 ID"派生。**默认/未设置时是 `"111111111111"` → RandomA=RandomB=[1,1,1,1]**。先按这个试；若设备拒绝 (checkId 返回非 1)，说明它记住了别的 ID，需用真实绑定手机号后 8 位。
- cipher 是 0..255 的单字节，**实在不行可暴力穷举 256 个值**：对一帧 0x07/0x37 推送数据解密后看 checksum (`ReceiveSum`) 是否自洽、`Iw/Ib/T` 是否落在合理范围，即可定位正确 cipher。这是绕过握手最稳的办法。

---

## 2. 开始采集 / 拉取血糖命令

CT5 有两种取数：**实时推送 (push)** 与 **主动拉取 (pull/fast-pull)**。

### 2.1 实时推送 (push) —— 设备主动上报
握手 + 校时完成、`SynchronizationState==4` 时，设备会主动通过 notify 推送 **命令 0x07** 数据帧。App 每收到一帧 0x07 就回一个 **推送响应通知**：

- `sendPushResponseNotification` → `pushResponseNotification_CT5()` = **`{0x35, 0x55, 0xAA, 0x34}`** (`ProtocolToolsHolder.java:1408`)。
- 见 `CGMDeviceHandlerCT5.handlePushData` (`:22-28`)：每次处理完 push 数据都 `sendPushResponseNotification()` 作为 ACK。

> 所以"让设备持续吐实时血糖"的最小闭环：完成握手→设备开始推 0x07→你每收一帧就回写 `35 55 AA 34`。

### 2.2 主动拉取历史 (fast pull) —— 命令 0x37
`fastPullGlucose(id, …)` → 经 MTU 协商后 `pullGlucose_series_request(startId+1, count)` → `pullGlucoseRequest_series_CT5(i10, i11)` (`ProtocolToolsHolder.java:917`)：

- 帧 5 字节：`[0]=0x37`，`[1]=startId低8位`，`[2]=startId高8位`(小端)，`[3]=count(本批要几条, ≤上限)`，`[4]=checksum(sum0..3)`。
- count 由 voltage 模式决定：`f30338c==0→1, ==1→33, else→45` (`CGMDeviceSenderAgreementD.java:152`；常量见 `Constants.FAST_REQUEST_SIZE_CT5=45 / _2=33`)。
- 设备回 **命令 0x37** 批量帧 → `handleFastPullData` → 解析见 §3.2。返回空表示拉完。

### 2.3 单条拉取 —— 命令 0x36
`pullGlucose(id)` → `pullGlucose_request` 对 CT5 走基类 (注意 `ProtocolTools.pullGlucose_request` 对 CT5 没专门分支，回落到 `pullGlucoseRequest(i10)` = `{0x55, idHi, idLo, checksum}`)。设备回 **0x36** → `handlePullData` (`CGMDeviceHandlerAgreementD.java:241`)，按单条 CT5 帧解析 (`getCurrentGlucoseCT5`)。
> 实战中 CT5 主用 push(0x07)+fastpull(0x37)，0x36 单条用得少。

### 2.4 其它握手期命令 (字节表见 §5)
版本 0x01、校时 0x03、check 0x05、init 0x06、低功耗 0x0F、解绑 0x0A、复位 0x11、SSN 0x3F、setId 0x30、checkId 0x31、setConfig 0x38、设备名 0x3B。

---

## 3. 血糖数据帧解析

### 3.0 解密前置 (所有血糖/SSN/config 帧通用)
密文段从 **第 4 字节 (index 3)** 开始 (前 3 字节 `[cmd, idLo, idHi]` 不加密)。解密：
```
plain = ConvertTools.encode(cipher_segment, cipher)   // encode 是"解密"方向
```
`ConvertTools.encode(bytes, cipher)` 逻辑 (`ConvertTools.java:90`)：
1. 每个字节先 `b ^ cipher`，转成 8-bit 二进制串拼接；
2. 从左到右扫描：若**右邻位为 0**，则翻转当前位 (0↔1) —— 一种相邻位去抖/解扰；
3. 每 8 bit 重新打包成字节。
`decode()` 是其逆 (先位操作再 XOR)，用于"加密"App→设备方向 (setConfig)。
> Python 完全可复现，无需 so。

解密后重新拼回 `[cmd, idLo, idHi] + plain + 重算checksum`，再按下表逐字段读。**多字节整数除特别注明外均为大端 (ByteBuffer 默认 BIG_ENDIAN)；glucoseId 例外是小端**。

### 3.1 单条当前血糖帧 (push 0x07 / 单拉 0x36) —— 两种长度

设备按是否带电压诊断分两种长度，代码用 `bArr.length==19 ? voltage模式 : 普通` 判断 (`CGMDeviceHandlerAgreementD.java:209/283`)。

#### (a) 普通 15 字节帧 — `getGlucoseByTransmitter_CT5` (`ProtocolToolsHolder.java:374`)
解密后按下表 (`wrap.get()` 顺序)：

| 偏移(解密后) | 字段 | 说明 |
|---|---|---|
| 0 | cmd | 0x07 或 0x36 |
| 1–2 | **glucoseId** | `lo + (hi<<8)`，**小端**，u16 |
| 3–4 | Ib | u16 大端 ÷100 → float (基线电流) |
| 5–6 | Iw | u16 大端 ÷100 → float (工作电流) |
| 7 | T 整数部 | `(u8 - 40)` |
| 8 | T 小数部 | `u8 ÷100`，与上相加 = 温度℃ |
| 9 | trend+nibble | 高半字节 = 错误/计数标志(b10)，低半字节(b11)→趋势 |
| 10 | glu 低字节 | 与 9 的低 nibble 组成 `GluMG = (lowNibble<<8) + byte10` |
| 11 | errorCode | 见 §3.4 错误码映射 |
| 12 | reserved/状态 | (普通帧到此，第 13 字节是校验) |
| 14 | checksum | |

**血糖值**：`GluMG`(整数, **mg/dL**) = `(byte9低nibble << 8) + byte10`；
`Glu`(float, **mmol/L**) = `GluMG ÷ 18`，保留 1 位 (`:409` / `:524`)。
即设备直接下发 mg/dL 整数，mmol/L 由 ÷18 得到。

#### (b) 电压诊断 19 字节帧 — `getGlucoseByTransmitter_CT5_voltage` (`:489`)
前 12 字节同上 (0..11)，之后多 5 个诊断字段 (`:601-605`)：

| 偏移 | 字段 | 换算 |
|---|---|---|
| 13 | BEVoltage | `u8 * 6` |
| 14 | WEVoltage | `u8 * 6` |
| 15 | REVoltage | `u8 * 6` |
| 16 | CEVoltage | `u8 * 6` |
| 17–18 | bVoltage | `(b17<<8)+b18` |
| (19) | checksum | |

### 3.2 批量拉取帧 (fast pull 0x37) — `pullResponseFromTransmitter_CT5` (`:1116`)

帧结构：`[0]=0x37, [1]=startId低, [2]=startId高, then N 条记录, 末尾 checksum]`。
- 先 `ConvertTools.isLegal()`/`isLegalV()` 把 payload 按 **11 字节 (普通) 或 15 字节 (电压)** 切块，剔除全 `FC`(空槽) 与全 `FF`(无效) 的块，重新拼接 (`ConvertTools.java:123/161`)。
- 解密同 §3.0。记录数 `(len-4)/11` 或 `/15`。
- 起始 `glucoseId = (b1 + (b2<<8)) & 0xFFFF`(小端)，逐条 +1。
- **每条 11 字节记录** 字段与 §3.1(a) 的 1..11 完全一致 (Ib, Iw, T整, T小, trend nibble, glu低, errorCode)；**15 字节记录**额外含 4×电压+bVoltage (同 3.1b)。
- 末条 `errorCode` 区段若 `Iw>655 && Ib>655 && T>215` 视为结束哨兵 → 停止 (`:1151`)。

### 3.3 字节序 / 缩放 / 单位 汇总
- glucoseId：**小端 u16**。
- Ib / Iw：**大端 u16 ÷ 100** → float (电流，单位 nA 量级，非血糖)。
- 温度 T：`(整数字节-40) + 小数字节/100` ℃。
- 血糖：`GluMG` = mg/dL 整数 (大端，由 nibble+1 字节拼，最大 ~4095)；`Glu` = mmol/L = GluMG/18。
- 电压：原始 u8 × 6 (mV)。
- **无 BCD、无浮点编码**，全是定点整数 + 求和校验 + 上述位扰加密。

### 3.4 trend / error 映射 (供参考)
- trend (byte9 低 nibble，`b11`)：0=无,1=快降低值,2=快降,3=慢降,4=平稳,5=慢升,6=快升,7=无(BGI计数)。
- errorCode (解密后某字节)：1=数据错,2=算法数据错,4=初始化完成,5=周期结束,11=噪声,12=灵敏度衰减,13=脱落,14=破损,15=触碰,16=进水,102=电流过大,103=电流过小,105=恢复，其余=正常。(见 `getGlucoseByTransmitter_CT5` switch)

---

## 4. 命令 / 帧整体格式

```
请求 (App → 0x1002 写):
  ┌──────┬───────────────┬──────────┐
  │ cmd  │ payload (变长)│ checksum │     无长度字段、无 CRC
  │ 1B   │  0..N B       │ 1B       │
  └──────┴───────────────┴──────────┘
  checksum = (Σ 所有前置字节) & 0xFF      // ReceiveSum(bArr,0,len-2)

应答 / 推送 (设备 → 0x1001 notify):
  [cmd 回显] [payload(可能加密, 从index3起)] [checksum]
  分发依据 = bArr[0] (handleData switch)
```

- **简单命令模板** `{cmd, 0x55, 0xAA, cmd-1}`：version=01,date=03(注意 date 是带时间的长帧),check=05,init=06,lowpower=0F,unbind=0A,reset=11,SSN=3F,pushResp=35,deviceName=3B。
- **带 ID 的命令** (pull/fastpull/inputBGMG)：id 用**小端** 2 字节。
- **加密命令** (setId 0x30, setConfig 0x38) 与**加密应答** (0x07/0x36/0x37/0x3F/0x38)：payload 第 4 字节起经 `ConvertTools` 位扰+XOR(cipher)。
- 合法性校验 `isLegal`：`bArr[last] == ReceiveSum(bArr,0,len-2)` (`ProtocolToolsHolder.java:832`)。

### handleData 命令分发表 (`CGMDeviceHandlerAgreementD.java:100`)
| cmd | 含义 | 处理 |
|---|---|---|
| 0x01 | 版本 | handleVersion |
| 0x02 | 设备名/BSN | handleCheckBSN |
| 0x03 | 校时 | handleDateWrite |
| 0x05 | check | handleCheck |
| 0x06 | init | handleInit |
| **0x07** | **实时推送血糖** | **handlePushData** |
| 0x0A | 解绑 | handleUnbind |
| 0x0F | 低功耗 | handleLowPower |
| 0x11 | 复位 | handleDeviceRest |
| 0x30 | setId | handleSetId (算 cipher) |
| 0x31 | checkId | handleCheckId |
| 0x35 | (推送ACK，仅发不收) | — |
| **0x36** | **单条拉取血糖** | **handlePullData** |
| **0x37** | **批量拉取血糖** | **handleFastPullData** |
| 0x38 | setConfig | handleConfig |
| 0x3F | SSN/传感器码 | handleSSN |

filt() 白名单 (`:43`)：只有 cmd ∈ {1,2,3,5,6,7,10,15,17,48,49,53,54,55,56,63} 的 notify 才被处理 (48=0x30,49=0x31,53=0x35,54=0x36,55=0x37,56=0x38,63=0x3F)。

---

## 5. CT5 命令字节速查表 (全部来自 `ProtocolToolsHolder`)

| 名称 | 命令码 | 字节 (十六进制) | 源 行号 |
|---|---|---|---|
| 版本查询 | 0x01 | `01` (CT2_5模板) | :633 |
| 校时 | 0x03 | `03 <yy-1900> <MM> <dd> <HH> <mm> <ss> <sum>` | :1488 |
| check | 0x05 | `05 55 AA 04` | :62 |
| init | 0x06 | `06 55 AA 05` | :706 |
| 低功耗 | 0x0F | `0F 55 AA 0E` | :840 |
| 解绑 | 0x0A | `0A 55 AA 09` 或 `0A <id4> <sum>` | :1603/:1611 |
| 复位查询 | 0x11 | `11 55 AA 10` | :1433 |
| setId | 0x30 | `30 <RandomB×4> <conv×4> <sum>` (10B) | :1516 |
| checkId | 0x31 | `31 <RandomB×4> <sum>` (6B) | :31 |
| 推送ACK | 0x35 | `35 55 AA 34` | :1408 |
| 单条拉取 | 0x55/0x36 | `55 <idHi> <idLo> <sum>` | :888 |
| 批量拉取 | 0x37 | `37 <idLo> <idHi> <count> <sum>` (5B) | :917 |
| setConfig | 0x38 | `38 <encrypted 12B> <sum>` (14B) | :1553 |
| 设备名 | 0x3B | `3B 55 AA 3A` | :233 |
| SSN | 0x3F | `3F 55 AA 3E` | :1412 |

---

## 6. 用 bleak 复现的步骤 (明确清单)

> 目标：连上 CT5，拿到实时血糖 (mg/dL & mmol/L)。

1. **扫描 & 连接**：按设备名前缀过滤 (CT5 名以 `WatchUtils.ZY_WATCH` 开头，`EDevice.java:139` 的 DEVICEANY；实测以你抓到的广播名为准)。连上后**协商 MTU=512** (`requestMtu(512)`，App 在 getSSN/fastpull 前都升 MTU)。
2. **订阅 notify** `00001001-1212-efde-1523-785feabcd123`。
3. **写命令到** `00001002-1212-efde-1523-785feabcd123`，**write-without-response**。
4. **握手取 cipher** (二选一)：
   - (推荐先试) 用默认 ID `"111111111111"` → RandomA=RandomB=[1,1,1,1]。发 `setId` (0x30)：payload = RandomB(4字节=01 01 01 01) + convolve(RandomB,RandomA) + checksum。收到 0x30 应答后按 `setIDResponse_CT5` 算 cipher (subArray(resp,5)→convolve(·,RandomA)→逐元素 XOR→&0xFF)。
   - (兜底) 跳过握手，直接等设备推 0x07，对收到的密文段**穷举 cipher 0..255**：用 `ConvertTools.encode` 解密后验 checksum 自洽且 Iw/Ib/T 合理，锁定 cipher。
5. **校验/校时** (可选但 App 会做)：发 checkId(0x31, RandomB)；成功后 setDate(0x03, 当前时间)。这两步能让设备进入正常推送状态。
6. **进入推送循环**：设备开始通过 notify 推 **0x07** 帧。每收到一帧：
   a. `isLegal` 校验 (末字节==前面求和);
   b. 取 `payload=frame[3:-1]`，`plain=encode(payload, cipher)`；
   c. 重组 `[frame[0],frame[1],frame[2]] + plain`，按 §3.1 解析 (15B 普通 / 19B 带电压);
   d. 读 glucoseId(小端)、Ib/Iw(÷100)、T、**GluMG (mg/dL)**、`Glu=GluMG/18 (mmol/L)`;
   e. **回写推送 ACK** `35 55 AA 34`，否则设备可能停推。
7. **补历史 (可选)**：发 `37 <idLo> <idHi> <count> <sum>` 批量拉，按 §3.2 (11B/15B 分块、剔除 FC/FF、解密) 解析。

伪代码核心：
```python
def receive_sum(b): return sum(b) & 0xFF

def encode(data, cipher):           # = 解密
    bits = ''.join(f'{(x ^ cipher) & 0xFF:08b}' for x in data)
    bl = list(bits)
    for i in range(len(bl)-1):      # 从左到右，右邻为0则翻转当前
        if bl[i+1] == '0':
            bl[i] = '1' if bl[i]=='0' else '0'
    bits = ''.join(bl)
    return bytes(int(bits[i:i+8],2) for i in range(0,len(bits),8))

def parse_glucose(frame, cipher):
    assert frame[-1] == receive_sum(frame[:-1])
    plain = encode(frame[3:-1], cipher)
    buf = bytes(frame[:3]) + plain
    gid = buf[1] + (buf[2] << 8)            # 小端
    ib  = ((buf[3]<<8)|buf[4]) / 100        # 大端
    iw  = ((buf[5]<<8)|buf[6]) / 100
    T   = (buf[7]-40) + buf[8]/100
    glu_mg = ((buf[9] & 0x0F) << 8) + buf[10]   # 注意 hex 拆 nibble，见原码 :397
    glu_mmol = round(glu_mg/18, 1)
    return gid, glu_mg, glu_mmol, ib, iw, T
```
> 注意 byte9 的 nibble 拆法：原码用 `HexadecimalTools.ByteToStr16` 把字节转 2 位十六进制串，高位字符→trend(b11)，低位字符补 0→GluMG 高 nibble(b10)。等价于：`trend = byte9 >> 4`，`glu_hi_nibble = byte9 & 0x0F`。需在真机数据上核对一次高低半字节方向。

---

## 7. 未确定 / 疑似 / 卡点

1. **【疑似，需真机验证】byte9 的 nibble 方向**：原码经字符串中转 (`ByteToStr16`)，`charAt(0)` 当 trend、`charAt(1)` 当 GluMG 高 nibble。即 `trend = byte9>>4`、`gluHiNibble = byte9&0x0F`。这点务必用一帧真实数据反推确认 (GluMG 应落在 ~36..4000 mg/dL = 2..22 mmol/L)。
2. **【未确定】RandomA/RandomB 真实取值**：依赖 App 写入设备的"通信 ID"(手机号/DeviceCommunicateId)。默认 `"111111111111"` 只是兜底。若设备已被官方 App 绑定过，它记的是真实手机号后 8 位，setId/checkId 用默认值会失败。**穷举 cipher (步骤 4 兜底) 可完全绕过此问题**。
3. **【native，但与拿血糖无关】** `AlgorithmTools.decodeCT` / `algorithm` 在 `libalgorithm_jni_1_29_0_0.so` (`AlgorithmTools.java:15`)。前者把 SSN 传感器码解成 K/R 标定值，后者做 AI 二次血糖估计。**实时血糖值 (GluMG/Glu) 由设备直接下发，不经过 so，可纯 Python 解析**。若你要复现 K/R 解码或 AI 值则需逆向该 so。
4. **【未确定】首次绑定的完整时序细节**：version→(saveTransmitterFirstInit)→setId→getSSN→setConfig→init→setDate 这条链涉及大量服务器交互 (`updateAnytime5SensorCode` 等)。复现仅取血糖不需要全走，关键只有 cipher + setDate + push ACK。
5. **【确认无误】UUID / write 类型**：服务 1000 / 写 1002 (write-without-response, `CGMManager.write` 用 `characteristic.getWriteType()`) / notify 1001，与你实测完全一致。
6. **checksum 是 8-bit 算术和**，不是 CRC/XOR，已确认 (`ReceiveSum`，`ProtocolToolsHolder.java:20`)。
```

