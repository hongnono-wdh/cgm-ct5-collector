# Yuwell CGM CT5 显示血糖换算逆向分析

> 目标：搞清楚官方 App 把设备推来的「原始 GluMG（Glu=GluMG/18）」变成「显示值」中间到底做了什么。
> 实测：Python 直读解出最近值 ~3.4 mmol/L，官方 App 显示 ~5 mmol/L，曲线形状一致、整体偏高约 1 mmol/L。

---

## 核心结论（先说答案）

**偏差不是简单的乘系数/加偏移，也不是"原始值本就一致、偏差另有原因"。**

**CT5 手机路径最终显示值 = native 算法 `libalgorithm_jni_1_29_0_0.so` 的 `algorithm(DataInput)` 重新算出来的 `GLU_MG`，再 /18。** 设备 BLE 推来的原始 `Glu/GluMG`（即 `getGlucoseByTransmitter_CT5` 设的那个值）**在落库/显示前被整段丢弃、不参与最终显示**。

也就是说，你解出的原始 GluMG ≠ App 显示值的来源。App 显示值是把**原始电流 Iw/Ib/温度 T**，连同 **K/R**（来自 sensor code 解码）和**历史电流序列 + 指尖血校准点**一起喂进 native .so 重算的结果。

属于你问题里的 **情况 (b)：必须逆向 native .so 的 `algorithm()`** 才能精确对齐 App 显示值。线性系数对齐（情况 a）不可行，因为 native 算法是带状态的（用历史电流、IIR、校准点、趋势、灵敏度衰减判定等），不是一个固定的 k·x+b。

---

## 1. CT5 手机路径：原始 Glu 到底被怎么处理

### 1.1 数据流总览（文件:行号）

```
设备 BLE 推 CurrentGlucose
  → CGMCallbackHandlerCT5.onNewGlucoseRead(...)            CGMCallbackHandlerCT5.java:766
  → onNewGlucoseData(currentGlucose, eDevice, z)           (基类)
  → lambda$onNewGlucoseData$17(...)                         CGMCallbackHandler.java:300
      ├─ convertGlucose(currentGlucose,...)                CGMCallbackHandler.java:843 / 955
      │     glucose.showGlu  = currentGlucose.getGlu_AI()   CGMCallbackHandler.java:986  ← 原始值（=GluMG/18）先放进去
      │     glucose.showGluMG= currentGlucose.getGluMG_AI() CGMCallbackHandler.java:987
      ├─ algorithm(convertGlucose, transmitter, setting)   CT5 override: CGMCallbackHandlerCT5.java:501
      │     → localAlgorithm(transmitter,setting,g,pocG,1) CGMCallbackHandlerCT5.java:507
      │       【localAlgorithm 在基类实现，jadx 默认解不出，见 1.2】
      ├─ handleNewGlucose(...) → insert(glucose) 落库      CGMCallbackHandler.java:1222
      └─ publish(...) 推给 UI
```

关键点：`convertGlucose` 里 `showGlu/showGluMG` 先被填成原始值（`Glu_AI`/`GluMG_AI`，即 `GluMG/18`），**但紧接着 `algorithm()→localAlgorithm()` 会把它整段覆盖掉**。

### 1.2 localAlgorithm —— 真正决定显示值的地方

`localAlgorithm` 在基类 `CGMCallbackHandler` 中，jadx 1.5.0 默认输出是 `throw new UnsupportedOperationException("Method not decompiled...")`（`jadx_out/.../base/CGMCallbackHandler.java:1263`）。
我用 `jadx --show-bad-code --single-class` 重新反编译，拿到了方法体（`F:\code\report\jadx_badcode\sources\com\yuwell\cgm\ble\cgm\callback\handler\base\CGMCallbackHandler.java:1246-1554`）。

它做的事（手机路径，三处分支逻辑一致）：

1. 构造 `DataInput`，喂进去：
   - `glucose.Iw / glucose.Ib / glucose.T`（原始电流 + 温度，单点或历史序列）
   - `transmitter.f30460k`(=K) 、`transmitter.f30461r`(=R)
   - 指尖血校准：`eventIds[]` + `BGMGs[]`（从校准 Event 取 `resulGlu*18`）
   - 算法切换升级时还塞 `reserved_keyword1/2`（历史 showGluMG）
   见 `CGMCallbackHandler.java(badcode):1288, 1340, 1397, 1500`
2. 调 native：`AlgorithmTools.getInstance().algorithm(dataInput)`（badcode:1368 / 1376 / 1421）
3. **用 native 返回的 DataOutput 覆盖显示字段**：
   ```java
   glucose.showGlu   = Tool.divide(algorithm.getGLU_MG(), 18.0f);   // badcode:1436 / 1475 / 1520
   glucose.showGluMG = Tool.getDecimal(algorithm.getGLU_MG(), 1);   // badcode:1438 / 1477 / 1522
   glucose.bg        = algorithm.getBG_MG_ADVICE()/18;
   glucose.warnCode/errorCode/trend/CalibrationStatus = ... (全来自 DataOutput)
   ```

**精确公式**：
```
显示值(mmol/L) = DataOutput.GLU_MG / 18
显示值(mg/dL)  = DataOutput.GLU_MG
其中 DataOutput = algorithm_jni_1_29_0_0.so::algorithm(DataInput{Iw[],Ib[],T[], K, R, eventIds[], BGMGs[], warmup_time, life_time, algorithm, ...})
```
参数来源：K/R 来自 sensor code（见 §3），Iw/Ib/T 来自设备原始电流，校准点来自本地 Event 表。**没有任何独立的"乘系数/加偏移"常量**——所有变换都封装在 .so 内部。

> 这解释了为什么偏差约 1 mmol/L 且曲线形状一致：native 算法相对原始 GluMG 做了一个与电流/校准/趋势相关的修正（很可能含温度补偿、灵敏度归一、校准回归），整体平移叠加轻微非线性，而非单纯线性缩放。

---

## 2. 指尖血校准：参数怎么存、怎么用

**结论：App 端不存"斜率/偏移/factor"。** 指尖血校准值是以**原始 mmol 值**存进本地表，然后在 §1.2 的 `localAlgorithm` 里作为输入点喂给 native 算法，由 .so 内部完成回归校准。

### 2.1 录入与保存（`BgCalibrationViewModel.java`）
- `saveBgGlucose(...)` → 先 `calibrateReference()` 判定当前是否允许校准（趋势/电流异常/初始化中等，行 139-188），再保存：
  - **`BgGlucose` 表**（ObjectBox）：字段 `blood_glucose_level`(float, 指尖血值)、`is_reference`、`measuring_point_id`、`measure_time`、`devicesn`、`EmitterSN`、`transid`、`effect`、`dataSource`。见 `lambda$saveBgData$13` 行 206-232。
  - **`Event` 表**：`eventId = enumEvent.BG`、`resulGlu = f10`(指尖血值, mmol)、`lastGluId/nextGluId`(对应的血糖点)、`deleteFlag`(code=="200" 无效值时置1)。见 `lambda$saveCalibrationEvent$14` 行 291-336。
- 同时 `z(glucose)`(行 508) 只是读 `glucose.CalibrationStatus` 判断是否已校准，**不写任何系数**。

### 2.2 校准如何套用到显示
不是 App 端套用，而是回到 `localAlgorithm`：
```java
// badcode CGMCallbackHandler.java:1325-1333
List<Event> calibrationList = eventRepository.getCalibrationList(transmitter.objId, glucose.time);
...
iArr2[i] = (int) Tool.multiply(R.resulGlu, 18.0f);   // 指尖血 mmol → mg/dL 整数
// 作为 DataInput 的 BGMGs[] 传给 native algorithm()
```
即：指尖血值 ×18 取整 → 作为 `BGMGs` 数组随对应 `eventIds` 一起进 native 算法，**校准的"斜率/偏移"在 .so 内部计算与保存，APK 层不可见**。

**所以：本地没有可直接读取的校准斜率/偏移字段。** 想知道校准后的修正，只能复算 native，或读 .so 维护的内部状态。

---

## 3. K/R 的作用

- **K/R 既下发给设备，也被 App 端 native 算法使用**——两者都用。
- 来源：sensor code 字符串经 `AlgorithmTools.decodeCT(char[])`(native) 解出 `KRDecodeData`，取 `getK()/getR()`。
  - 下发：`onKRRead(...)` → `PreferenceSource.setK/ setR` + `setConfig(k, r, id)` 下发设备。`CGMCallbackHandlerCT5.java:742-763`
  - 热更新：`lambda$updateAnytime5SensorCode$14` 解码后写 `transmitter.f30460k/f30461r`。`CGMCallbackHandlerCT5.java:330-341`
- 存储位置（两处）：
  - SharedPreferences：key `"k"` / `"r"`。`PreferenceSource.java:289,375,709,769`
  - **Transmitter 实体**：`transmitter.f30460k`(K)、`transmitter.f30461r`(R)。`Transmitter.java:79,85`（toJson 写成 `"k"`/`"r"`，行 434-435）
- **App 端用法**：`localAlgorithm` 把 `transmitter.f30460k / f30461r` 作为 `DataInput` 的 `K0 / R` 传给 native `algorithm()`（badcode:1288 等）。所以 App 端确实拿 K/R 对原始电流重算了一次。

### `KRDecodeData` 字段（`ist/com/sdk/KRDecodeData.java`）
`k`(float)、`r`(float)、`calibration`(int)、`lifeTime`(int)、`unitOrder`(int)、`year`(int)、`marketNo`、`serialNo`、`sensorNo`、`electrodeType`、`electrodeTecNo`、`enzymeTecNo`、`membraneTecNo`。

---

## 4. algorithm() / DataInput / DataOutput

**判断：CT5 手机端最终显示值 100% 走 native `algorithm()`。** 用户原以为"手机路径不调 algorithm()"是因为只看到 `WatchCGMController` 显式调用——但实际上手机路径通过基类 `localAlgorithm` 间接调用，且 jadx 默认把该方法体藏了（解码失败）。

### DataInput 喂进去的字段（`ist/com/sdk/DataInput.java` 构造器，参考 `WatchCGMController.java:854` 与 base `localAlgorithm:1288`）
```java
new DataInput(
  glucoseId,
  Iws[]  = {glucose.Iw}      // 工作电极电流（手机路径异常时换成历史序列 getIws()）
  Ibs[]  = {glucose.Ib}      // 背景电流
  Ts[]   = {glucose.T}       // 温度
  eventIds[] / BGMGs[]       // 指尖血校准点（glucoseId ↔ 指尖血mg/dL）
  K0 = transmitter.f30460k   // K
  R  = transmitter.f30461r   // R
  startTimeMillis, transmitterName)
// setTransmitterName 再推导 warmup_time / life_time / algorithm(算法编号)
// 升级场景额外 setReserved_keyword1/2（历史 showGluMG 序列）
```

### DataOutput 吐出（`ist/com/sdk/DataOutput.java`）
`GLU_MG`(int, **最终显示血糖 mg/dL**)、`BG_MG_ADVICE`(int)、`BGCount`、`BGICount`、`warnCode`、`errorCode`、`trend`、`calibrationStatus`、`data_quality`、`dayCount`、`hourCount`、`hypoglycemiaEarlyWarnMinutes`、`hyperglycemiaEarlyWarnMinutes`。

**显示值就是 `DataOutput.GLU_MG`**（mmol = /18）。

### 手机路径 vs 手表路径
- 手表：`WatchCGMController.U()`（=localAlgorithm）`WatchCGMController.java:849-918`，逻辑与基类几乎一致（同样 `showGlu = GLU_MG/18`，行 896-897）。
- 手机 CT5：基类 `CGMCallbackHandler.localAlgorithm`（badcode:1246），被 `CGMCallbackHandlerCT5.algorithm()`(行 501-507) 调用。
- 两条路径都调同一个 native `algorithm(DataInput)`、都用 `GLU_MG` 覆盖 `showGlu/showGluMG`。

---

## 5. 结论与对齐 App 显示值的方案

| 方案 | 可行性 | 说明 |
|------|--------|------|
| (a) 读本地斜率/偏移线性换算 | ❌ 不可行 | App 不存斜率/偏移；校准点是喂进 native 的输入，修正系数封在 .so 内部 |
| (b) 逆向 native `algorithm()` | ✅ 唯一精确路线 | 显示值 = `algorithm(DataInput).GLU_MG / 18`，必须复算 native |
| (c) 原始值本应一致 | ❌ 不成立 | 原始 `Glu_AI`/`GluMG_AI` 在落库前被 native 输出整段覆盖 |

### 最可能情况：**(b)**

**理由**：
1. `localAlgorithm`（手机/手表路径都走）明确用 native `algorithm()` 的 `GLU_MG` 覆盖 `showGlu/showGluMG`（badcode:1436/1438 等），原始 GluMG 不参与显示。
2. 算法是**带状态/带历史**的：用历史电流序列(`getIws/getIbs/getTs`)、IIR/趋势、灵敏度衰减、温度补偿、校准回归——不可能等价为固定的 `k·x+b`。这与"偏差约 1mmol/L 但曲线形状一致"吻合：是温度/灵敏度/校准修正叠加的近似平移而非纯线性缩放。
3. 校准也只是把指尖血点喂进 .so，App 层看不到校准产物。

### 落地建议（按成本递增）
1. **最省事**：既然你能直读设备，App 的"显示值"其实已落本地 ObjectBox `Glucose` 表的 `showGlu/showGluMG` 字段（`Glucose.java:48-49`）。若能从手机导出该数据库（或抓 App 上传后端的同步报文），直接读 `showGlu` 即可，无需复算。
2. **半精确近似**：在你已有原始 GluMG 基础上，用同时刻 App `showGluMG` 做配对，拟合一个时变/分段修正（接受非线性误差），仅用于粗略对齐。
3. **完全精确**：用 Frida hook `ist.com.sdk.AlgorithmTools.algorithm(DataInput)` 的入参/返回，或静态逆向 `libalgorithm_jni_1_29_0_0.so` 的 `algorithm` 实现，自行复算 `GLU_MG`。这是唯一能在没有 App、没有数据库的情况下精确对齐的方法。

### 待确认 / 不确定项
- `Glu_AI` 与 native `GLU_MG` 的具体数值差，需用真机日志（`localAlgorithm` 有 `Logger.i("input 计算后的数据 " + algorithm)` 打印 DataOutput）对比验证——建议抓一次 logcat 看 `GLU_MG` 与设备原始 `GluMG` 差值，确认偏差就是 native 修正量。
- native `algorithm()` 内部的具体数学（是否含 K/R 线性项 + 温度项 + 校准回归）未逆向，无法在本报告给出 .so 内部公式。
- `transmitter.f30460k/f30461r` 在你的设备上的实际值未知；若为 0，native 可能走默认 K（参考 `CGMCallbackHandlerCT5.java:335` 的 `if(k==0) k=transmitter.f30460k` 兜底）。
