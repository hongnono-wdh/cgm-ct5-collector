# Frida/逆向 decodeCT 拿 K/R —— 可行性评估报告

目标:脱离官方 App 自行获取传感器标定值 K/R(用于自激活新传感器)。
评估对象:`libalgorithm_jni_1_29_0_0.so`(arm64-v8a)里的 `decodeCT(char[] SSN) → KRDecodeData`。

## 环境约束
- 手机 Realme RMX3706 / ColorOS,**未 root**(`su` 不可用),只有 Shizuku(ADB 级,非 root)。
- → **frida-server 路线被堵**(它要 root)。

## .so 静态分析结论
| 函数 | 大小 | 特征 |
|---|---|---|
| `Java_..._decodeCT` | 1692B | **按符号名静态导出**(非动态注册),Frida 易 hook。用 regex(regcomp/regexec)解析 SSN,调 `hfhreiRE`/`hfhreiRERjz` 做解码,JNI 经 env 指针间接调 |
| `hfhreiRE` | 392B / 98指令 | 32浮点, 仅1分支, 近直线。`ldrb→scvtf→fmul/fadd→strb`,即对SSN字节做**系数加权多项式** |
| `hfhreiRERjz` | 356B / 89指令 | 31浮点, **0分支0调用全直线**。同为线性组合 |
- K 是 float(`setK (F)V`),K0 是 float[](`getK0 ()[F`),R 是 int[](`getR ()[I`)。
- .rodata 含大量 IEEE754 双精度系数(`...33333`/`...fffff` 字节特征)= 标定系数表。
- **无网络、无设备状态依赖**:decodeCT 是 SSN 字符串的纯函数。

## 可行性结论:高度可行,且静态逆向优于 Frida

| 路线 | 可行性 | 说明 |
|---|---|---|
| **A. 静态逆向重写成 Python**(推荐) | ★★★★ | 核心仅 2 个直线小函数(~750B)+regex+系数表,公式形如 `K=Σ(digit_i×coef_i)`。提取常数+理顺数据流即可纯 Python 复现。**不用手机/root/Frida** |
| B. Frida 调真 decodeCT | ★★★ | decodeCT 按名导出、Frida 易调;但无 root → 必须把 frida-gadget 注入**重打包的 APK**(apktool+重签名),较繁琐/易碎 |
| C. 直接调/模拟 hfhreiRE+RERjz | ★★★ | 纯C无JNI,可用 unicorn 模拟喂 SSN;省去 JVM,但要搭模拟环境 |
| D. 自建最小 harness APK 调 decodeCT | ★★★ | 自己的可调试 app,免 root;但要 Android 构建工具链 |

## 验证与风险
- **测试向量**:可从设备 querySSN(0x3F)读当前传感器 SSN(我们已有协议+cipher),不需要新传感器即可测 decodeCT 重写是否合理。
- **真值校验**:App 把 K/R 存在 SharedPreferences(`"k"/"r"`)和 Transmitter 对象——无 root 难直接读;最终真值校验要么 Frida 跑一次真 decodeCT 对比,要么靠"自激活后血糖是否正确"(有废传感器风险)。
- **K/R 只是自激活的一环**:还要正确做 setId+setConfig+init,且可能涉及服务器注册。即便拿到 K/R,完整自激活仍有废传感器风险——建议先在愿意冒险的传感器上验证。

## 建议下一步
1. 读取当前传感器 SSN(querySSN),作为测试向量。
2. 全量带操作数反汇编 hfhreiRE/hfhreiRERjz,解析 .rodata 系数,提取公式,Python 复现 decodeCT。
3. 用 SSN 跑出 K/R,与设备观测标定(GluMG≈Iw×K、App≈原始×1.327)互校。
