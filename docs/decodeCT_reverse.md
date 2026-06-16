# 逆向 `decodeCT` —— 鱼跃 CGM 传感器序列号 (SSN) → 标定值 K/R

目标库：`so_analysis/lib/arm64-v8a/libalgorithm_jni_1_29_0_0.so`（arm64-v8a ELF）。
纯静态反汇编（capstone + pyelftools），脱离 .so/JVM 用纯 Python 复现。

涉及函数：
| 符号 | vaddr | 作用 |
|---|---|---|
| `Java_ist_com_sdk_AlgorithmTools_decodeCT` | `0x67a4` (1692B) | JNI 入口：取 SSN char[]，regex 选格式，调解码函数，回填 KRDecodeData |
| `hfhreiRE`    | `0x81880` (392B) | **格式 1** 解码（17/18 位 SSN） |
| `hfhreiRERjz` | `0x81a08` (356B) | **格式 2/3** 解码（21 位 SSN） |

> 重要更正：`hfhreiRE`/`hfhreiRERjz` 不是"对若干字节做加权多项式"的标定算法，而是 **SSN 字段切分器**——把 SSN 各字符切成 marketNo / year / serialNo / K / R 等字段写进一个原生结构体。K、R 是把 SSN 里的若干 ASCII 数字按"十进制小数位"拼出来再做定点四舍五入，**不是对电流的多项式拟合**。

---

## 1. PLT 解析（确认调用对象）

用 `.rela.plt` 把 PLT 桩映射到符号：

```
plt 0x5b00 -> regcomp     plt 0x6040 -> regexec    plt 0x5e80 -> regfree
plt 0x5b50 -> calloc      plt 0x6360 -> free       plt 0x61f0 -> strlen
plt 0x5f80 -> hfhreiRERjz plt 0x5fc0 -> hfhreiRE   plt 0x5f10 -> __stack_chk_fail
```

注意：题目给的"调 0x5f80 / 0x5fc0 两个自定义解码函数"中——**0x5fc0 = hfhreiRE（格式 1），0x5f80 = hfhreiRERjz（格式 2/3）**。

---

## 2. decodeCT 控制流与 regex 模式

decodeCT 流程（关键片段）：

```
0x6870  adrp x27,#0x17f000 ; ldr x27,[x27,#0xff0]   ; x27 = *(0x17fff0) = 全局结果结构体指针
0x687c  movi v0.2d,#0 ; stp q0,q0,[x27] ; stur q0,[x27,#0x1c] ; 把结构体 +0..+0x2b 清零（48字节）
0x6880  adrp x1,#0x111000 ; add x1,x1,#0xe1d         ; 模式1 字符串
0x6894  bl 0x5b00 (regcomp) ; ... bl 0x6040 (regexec)
0x68b0  cbz w0,#0x6938  ; regexec==0(匹配) -> 0x6938 调 hfhreiRE
        否则 regfree，试模式2（0x111e8b），匹配 -> 0x6924 调 hfhreiRERjz
        否则试模式3（0x111f0e），匹配 -> 同样走 hfhreiRERjz
        全不匹配 -> 返回 null（x20=0）
0x6938  x0=结构体, x1=parsed_ssn, bl 0x5fc0 (hfhreiRE)        ; 格式1
0x6924  x0=结构体, x1=parsed_ssn, bl 0x5f80 (hfhreiRERjz)     ; 格式2/3
```

三个模式串（adrp+add 指向 .rodata 解析）：

- **模式 1** `0x111e1d`（长度 17 或 18）：
  ```
  ^[A-Z0-9][0-9](0[1-9]|[1-9][0-9]|[A-Z][1-9A-Z])(00[1-9]|0[1-9][0-9]|[1-9][0-9][0-9]){2}[0-9]{4,5}[0-9A-Z]{3}$
  ```
- **模式 2** `0x111e8b`（长度 21，首字符属于特定集合）：
  ```
  ^[1-9A-Zabdefytsrq][1-9][A-Z0-9][0-9](0[1-9]|[1-9][0-9]|[A-Z][1-9A-Z])(00[1-9]|0[1-9][0-9]|[1-9][0-9][0-9]){2}[0-9]{6}[0-9A-Z]{3}$
  ```
- **模式 3** `0x111f0e`（长度 21，以 `00` 开头）：
  ```
  ^00[A-Z0-9][0-9](0[1-9]|[1-9][0-9]|[A-Z][1-9A-Z])(00[1-9]|0[1-9][0-9]|[1-9][0-9][0-9]){2}[0-9]{6}[0-9A-Z]{3}$
  ```

模式 1 段结构（17 位）：`[lead1][digit1] [2位组] [3位组×2=6位] [4或5位数字] [3位字母数字]`。`[0-9]{4,5}` 决定 strlen=17 或 18，hfhreiRE 用 `cmp x0,#0x12`(18) 分支。

decodeCT 回填的字段全部来自这个清零过的全局结构体（固定偏移），与两个解码函数写入的偏移一致：

| setter（JNI 调用顺序） | 结构体偏移 | 类型 |
|---|---|---|
| setElectrodeType | +8 | char |
| setLifeTime | +4 | int |
| setMarketNo | +0 | char |
| setYear | +0xc | int |
| setSerialNo (GetStringUTFChars @+0x10) | +0x10 | str |
| setCalibration | +0x1c | int |
| setK (float→double) | +0x20 | float |
| setR (float→double) | +0x24 | float |
| setUnitOrder | +0x14 | int |
| setSensorNo (@+0x18) | +0x18 | str |
| setElectrodeTecNo | +0x28 | char |
| setEnzymeTecNo | +0x29 | char |
| setMembraneTecNo | +0x2a | char |

---

## 3. 常数提取

K/R 的浮点常数不是 adrp+ldr 取 .rodata double，而是 **fmov/movk 内联立即数**（单精度 float）：

| 指令编码 | float 值 |
|---|---|
| `fmov s,#10.0` | 10.0 |
| `fmov s,#0.5` | 0.5 |
| `mov w,#0xcccd; movk w,#0x3dcc,lsl#16` → `0x3dcccccd` | 0.1 |
| `mov w,#0xd70a; movk w,#0x3c23,lsl#16` → `0x3c23d70a` | 0.01 |
| `mov w,#0x42c80000` | 100.0 |
| `mov w,#0x64`(整数) | 100 |
| `mov w,#0xa`(整数) | 10 |
| `mov w,#-0x14d0`(整数) | -5328 |

`*scale → +0.5 → fcvtzs（截断）→ /scale` 是定点四舍五入：scale=10 保留 1 位小数，scale=100 保留 2 位。

---

## 4. 公式还原（字符串字节 → 字段）

记 `d(i) = ssn[i] - '0'`（ASCII 数字转 int），`c(i) = ssn[i]`（原字符），`round_n` = 保留 n 位小数的四舍五入（float32）。

### 格式 1 — hfhreiRE（字节下标 0 起）

```
electrodeType = c(0)                         ; +8
year          = d(1)                         ; +0xc   （注意：fmt1 这个 int 来自 byte1）
serialNo      = c(2)c(3)                      ; +0x10  （2 字符）
unitOrder     = d?* : ord(s5)*10 + ord(s4)*100 + ord(s6) - 5328  ; +0x14
                即 unitOrder = d(5)*10 + d(4)*100 + d(6)   （-5328 = -0x14d0 抵消三个 '0'×加权）
sensorNo      = c(7)c(8)c(9)                  ; +0x18  （3 字符）

K (+0x20):
    K = round_1( 0.1*d(11) + d(10) )
    若 strlen==18:  K = round_2( K + 0.01*d(12) )   ; 并把 R 起点右移 1
R (+0x24):
    base = 13 if strlen==18 else 12
    R = round_1( 0.1*d(base+1) + d(base) )

electrodeTecNo = c(base+2) ; enzymeTecNo = c(base+3) ; membraneTecNo = c(base+4)

未写入（读回 0）：marketNo(+0)、lifeTime(+4)、calibration(+0x1c)
```

> `unitOrder` 反汇编：`mul w8,w8,#0xa`(=d5字符值*10? 实为 ord*10) `madd w8,w10,#0x64,w8`(+ord(s4)*100) `add w8,w8,w11`(+ord(s6)) `add w8,w8,#-0x14d0`。
> 因 `ord(s4)*100 + ord(s5)*10 + ord(s6)` 中三个 ASCII '0'(0x30) 的加权和 = 0x30*100+0x30*10+0x30 = 5328 = 0x14d0，减掉即得纯数字 `d(4)*100+d(5)*10+d(6)`。

### 格式 2 / 3 — hfhreiRERjz（字节下标 0 起）

```
marketNo      = c(0)                          ; +0
lifeTime      = d(1)                           ; +4
electrodeType = c(2)                           ; +8
year          = d(3)                           ; +0xc
serialNo      = c(4)c(5)                        ; +0x10
unitOrder     = d(6)*100 + d(7)*10 + d(8)       ; +0x14  （同上 -5328 抵消）
sensorNo      = c(9)c(10)c(11)                  ; +0x18
calibration   = d(12)                           ; +0x1c

K (+0x20):  两段
    t = round_1( 0.1*d(14) + d(13) )
    K = round_2( 0.01*d(15) + t )
R (+0x24):
    R = round_1( 0.1*d(17) + d(16) )

electrodeTecNo = c(18) ; enzymeTecNo = c(19) ; membraneTecNo = c(20)
```

---

## 5. 关键反汇编片段（K/R 计算，hfhreiRE）

```
; K: 0.1*d(11) + d(10)，round_1
0x818cc ldrb w8,[x20,#5]; 0x818d0 ldrb w10,[x20,#4]; 0x818d4 ldrb w11,[x20,#6]   ; unitOrder 取字节
0x8191c ldrb w8,[x20,#0xa]; 0x81918 ldrb w9,[x20,#0xb]
0x81924 sub w8,w8,#0x30 ; 0x81920 sub w9,w9,#0x30        ; -'0'
0x8192c fmul s1,s2,s1   (s1=0.1, s2=d(11))               ; 0.1*d(11)
0x81934 fadd s2,s1,s2   (+ d(10))
0x8193c fmov s1,#0.5; 0x8193c fmul s2,s2,#10; fadd; fcvtzs; scvtf; fdiv /10   ; round_1
0x81950 b.ne 0x81998    (strlen!=18 跳过第三位)
; strlen==18: + 0.01*d(12) 再 round_2(*100)
0x81964 sub w9,..,#0x30; fmul s3(=0.01)*d(12); fadd; fmul *100; +0.5; fcvtzs; /100
0x8199c str s2,[x19,#0x20]    ; 写 K
; R 同理：0.1*d(base+1)+d(base) round_1 -> str s2,[x19,#0x24]
```

---

## 6. Python 实现与自检

实现见 `F:\code\report\decodeCT.py`，核心 API `decode_ct(ssn:str)->dict|None`。所有浮点用 `struct` 截断到 float32 以匹配 .so 的单精度行为；`round_n` 精确复制 `*scale +0.5 fcvtzs /scale`。

自检（构造合法 SSN，`python decodeCT.py`）：

| SSN | 格式 | K | R | 合理性 |
|---|---|---|---|---|
| `A1050790015234ABC` (17) | 1 | 5.2 | 3.4 | K~5 量级合理 |
| `A10507900152349ABC` (18) | 1 | 5.23 | 4.9 | 18 位走两位小数分支，符合 |
| `A21005079001234567ABC` (21) | 2 | 3.45 | 6.7 | K=3.45 (digit13/14/15="345"), R=6.7 ("67") |
| `001005079001234567XYZ` (21) | 3 | 3.45 | 6.7 | 同 fmt2 算法 |

K 落在 ~1–10，R ~2–7，与"传感器电流(nA)→mg/dL 斜率/截距系数"量级吻合。

---

## 7. 把握度 / 存疑标注

**确定（指令级一一对应）：**
- 三个 regex 模式串（直接读自 .rodata）。
- K/R 计算公式与定点四舍五入（fmov/movk 立即数全部解出，无 .rodata double 依赖）。
- 两种格式的全部字节→结构体偏移映射，以及 decodeCT 读哪个偏移调哪个 setter（已逐条核对调用顺序与 +offset）。
- float32 截断行为。

**存疑 / 需真值验证：**
- **字段语义命名**：偏移→setter 是确定的，但"格式 1 把 byte1 当 year、marketNo/lifeTime/calibration 读回 0"这一点——是源于格式 1 的 `hfhreiRE` 根本不写这些偏移（结构体被清零），所以这些字段在格式 1 下确实为 0/空。这在指令层是确定的，但**业务含义上是否真的"格式 1 没有 marketNo"** 无法仅从 .so 断言（可能格式 1 的 SSN 本就不携带这些信息）。
- **serialNo / sensorNo 只截了 2/3 个字符**：decodeCT 用 `GetStringUTFChars`/`NewStringUTF` 从 +0x10、+0x18 取「以 NUL 结尾的 C 字符串」。结构体里 +0x10 连续写了 2 字节、+0x12 起是别的字段，所以 serialNo 实际是「+0x10 起到下一个 0 字节」——长度取决于运行时相邻字节，静态无法 100% 确定串长。我按"写入的字符数"截取（fmt1=2、fmt2=2 给 serialNo；sensorNo=3），**可能与真机 GetStringUTFChars 读到的实际长度不同**（若相邻字节非 0 会更长）。这是最大的不确定点。
- **K 的 round 顺序细节**：格式 2 的 K 是两段四舍五入（先 round_1 再 round_2），已按指令复刻；但缺真值，无法验证边界舍入是否逐位一致。
- **没有真实 SSN 样本**：上面测试串是按 regex 反推构造的合法串，K/R 数值"合理"但非厂商真值，未做端到端比对。

**卡点（诚实）：** 无法验证 serialNo/sensorNo 的真实字符串长度（依赖运行时 NUL 终止），以及没有真机/真 SSN 做 K/R 端到端校验。其余（regex、K/R 数值公式、字段偏移）在指令层是确定的。
