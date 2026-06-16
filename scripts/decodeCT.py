"""
Pure-Python reimplementation of the native `decodeCT` from
libalgorithm_jni_1_29_0_0.so (arm64-v8a).

Reverse-engineered from:
  Java_ist_com_sdk_AlgorithmTools_decodeCT @ 0x67a4
  hfhreiRE                                 @ 0x81880  (decoder for SSN format 1)
  hfhreiRERjz                              @ 0x81a08  (decoder for SSN format 2)

decode_ct(ssn) -> dict with K, R, calibration, lifeTime, marketNo, year,
serialNo, unitOrder, sensorNo, electrodeType, electrodeTecNo, enzymeTecNo,
membraneTecNo.

See decodeCT_reverse.md for the full disassembly-level derivation.
"""
import re
import struct

# ---- regex patterns extracted verbatim from .rodata ----
# 0x111e1d  (format 1, len 17)
PAT1 = r'^[A-Z0-9][0-9](0[1-9]|[1-9][0-9]|[A-Z][1-9A-Z])(00[1-9]|0[1-9][0-9]|[1-9][0-9][0-9]){2}[0-9]{4,5}[0-9A-Z]{3}$'
# 0x111e8b  (format 2, len 21, leading char in a set)
PAT2 = r'^[1-9A-Zabdefytsrq][1-9][A-Z0-9][0-9](0[1-9]|[1-9][0-9]|[A-Z][1-9A-Z])(00[1-9]|0[1-9][0-9]|[1-9][0-9][0-9]){2}[0-9]{6}[0-9A-Z]{3}$'
# 0x111f0e  (format 3, len 21, leading "00")
PAT3 = r'^00[A-Z0-9][0-9](0[1-9]|[1-9][0-9]|[A-Z][1-9A-Z])(00[1-9]|0[1-9][0-9]|[1-9][0-9][0-9]){2}[0-9]{6}[0-9A-Z]{3}$'


def _f32(x):
    """Truncate a Python float to IEEE754 single precision (the .so works in float)."""
    return struct.unpack('<f', struct.pack('<f', x))[0]


def _round_to(value, scale):
    """Replicates: x = floor(value*scale + 0.5) / scale  using float32 math.
    scale=10 -> 1 decimal place, scale=100 -> 2 decimal places."""
    value = _f32(value)
    scaled = _f32(_f32(value * scale) + 0.5)
    q = int(scaled)          # fcvtzs = truncate toward zero
    return _f32(_f32(float(q)) / scale)


def _d(ch):
    """ASCII digit char -> int (the 'sub #0x30' the code does)."""
    return ord(ch) - 0x30


def _decode_fmt1(ssn):
    """hfhreiRE @ 0x81880. Input SSN (format 1). Byte indices match [x20, #n].
    Writes the native struct at the following offsets (the rest stay 0):
      +8  = byte0 (char)              -> read by decodeCT as electrodeType
      +0xc= byte1 - '0' (int)         -> read by decodeCT as year
      +0x10,+0x11 = byte2,byte3 (chars) -> serialNo (2 chars)
      +0x14 = unitOrder (int)
      +0x18..0x1a = byte7,8,9 (chars) -> sensorNo
      +0x20 = K (float), +0x24 = R (float)
      +0x28..0x2a = electrode/enzyme/membrane TecNo (chars)
    NOTE: +0 (marketNo), +4 (lifeTime), +0x1c (calibration) are NOT written
          by this format -> they read back as 0.
    """
    s = ssn
    n = len(s)                         # strlen result, compared to 0x12 (18)
    out = {}
    out['o08_electrodeType'] = s[0]    # strb [x19,#8]  (byte 0)
    out['o0c_year']      = _d(s[1])    # str  [x19,#0xc] (byte1 - '0')
    out['o10_serialNo']  = s[2:4]      # bytes 2,3 chars at +0x10,+0x11
    # bytes 4,5,6 -> int at +0x14:  unitOrder = ssn[5]*10 + ssn[4]*100 + ssn[6] - 0x14d0
    b4, b5, b6 = ord(s[4]), ord(s[5]), ord(s[6])
    out['o14_unitOrder'] = b5 * 10 + b4 * 100 + b6 - 0x14d0
    out['o18_sensorNo'] = s[7:10]      # bytes 7,8,9 chars at +0x18,+0x19,+0x1a
    # ---- K (struct +0x20) ----
    k = _f32(_d(s[0xb]) * _f32(0.1) + _d(s[0xa]))   # 0.1*digit11 + digit10
    k = _round_to(k, 10)                            # round to 1 decimal
    if n == 18:
        k = _f32(k + _d(s[0xc]) * _f32(0.01))       # + 0.01*digit12
        k = _round_to(k, 100)                       # round to 2 decimals
        rbase = 0xd
    else:
        rbase = 0xc
    out['o20_K'] = k
    # ---- R (struct +0x24) ----
    r = _f32(_d(s[rbase + 1]) * _f32(0.1) + _d(s[rbase]))
    r = _round_to(r, 10)
    out['o24_R'] = r
    # bytes rbase+2, +3, +4 raw chars at +0x28,+0x29,+0x2a
    out['o28_electrodeTecNo'] = s[rbase + 2]
    out['o29_enzymeTecNo']    = s[rbase + 3]
    out['o2a_membraneTecNo']  = s[rbase + 4]
    return out


def _decode_fmt2(ssn):
    """hfhreiRERjz @ 0x81a08. Input SSN (format 2 / 3). Byte indices match [x1, #n]."""
    s = ssn
    out = {}
    out['o00_marketNo']  = s[0]            # strb [x0,#0]   byte0
    out['o04_lifeTime']  = _d(s[1])        # str  [x0,#4]   byte1 - '0' (int)
    out['o08_electrodeType'] = s[2]        # strb [x0,#8]   byte2
    out['o0c_year']      = _d(s[3])        # str [x0,#0xc]  byte3 - '0' (int)
    out['o10_serialNo']  = s[4:6]          # bytes 4,5 chars @ +0x10,+0x11
    # bytes 6,7,8 -> int @ +0x14: ssn[7]*10 + ssn[6]*100 + ssn[8] - 0x14d0
    b6, b7, b8 = ord(s[6]), ord(s[7]), ord(s[8])
    out['o14_unitOrder'] = b7 * 10 + b6 * 100 + b8 - 0x14d0
    out['o18_sensorNo'] = s[9:12]          # bytes 9,10,11 chars @ +0x18..0x1a
    out['o1c_calibration'] = _d(s[0xc])    # str [x0,#0x1c] byte12 - '0' (int)
    # ---- K (struct +0x20): two-stage ----
    # stage A: t = round1( 0.1*digit14 + digit13 )
    t = _round_to(_f32(_d(s[0xe]) * _f32(0.1) + _d(s[0xd])), 10)
    # stage B: K = round2( (0.01*digit15 + t) * 100 )/100   == round2(0.01*digit15 + t)
    k = _f32(_d(s[0xf]) * _f32(0.01) + t)
    k = _round_to(k, 100)
    out['o20_K'] = k
    # ---- R (struct +0x24): round1( 0.1*digit17 + digit16 ) ----
    r = _round_to(_f32(_d(s[0x11]) * _f32(0.1) + _d(s[0x10])), 10)
    out['o24_R'] = r
    # bytes 18,19,20 raw @ +0x28,+0x29,+0x2a
    out['o28_electrodeTecNo'] = s[0x12]
    out['o29_enzymeTecNo']    = s[0x13]
    out['o2a_membraneTecNo']  = s[0x14]
    return out


def decode_ct(ssn):
    """Decode a sensor serial number (SSN) string into K/R calibration data.

    Returns None if the SSN matches none of the 3 patterns (the native code
    returns null / does not call any setter in that case).
    """
    if not isinstance(ssn, str):
        ssn = ssn.decode('ascii')

    if re.match(PAT1, ssn):
        f = _decode_fmt1(ssn)
        fmt = 1
        # offsets the native struct leaves at 0 in format 1:
        marketNo = chr(0)        # +0  not written  (decodeCT reads byte 0 -> '\x00')
        lifeTime = 0             # +4  not written
        calibration = 0          # +0x1c not written
    elif re.match(PAT2, ssn) or re.match(PAT3, ssn):
        f = _decode_fmt2(ssn)
        fmt = 2
        marketNo = f['o00_marketNo']
        lifeTime = f['o04_lifeTime']
        calibration = f['o1c_calibration']
    else:
        return None

    # decodeCT reads these fixed struct offsets and calls the matching setter:
    result = {
        'format':         fmt,
        'marketNo':       marketNo,                 # +0   (1 char; 0 in fmt1)
        'lifeTime':       lifeTime,                 # +4   (int; 0 in fmt1)
        'electrodeType':  f['o08_electrodeType'],   # +8   (1 char)
        'year':           f['o0c_year'],            # +0xc (int)
        'serialNo':       f['o10_serialNo'],        # +0x10 (2 chars)
        'unitOrder':      f['o14_unitOrder'],       # +0x14 (int)
        'sensorNo':       f['o18_sensorNo'],        # +0x18 (3 chars)
        'calibration':    calibration,              # +0x1c (int; 0 in fmt1)
        'K':              f['o20_K'],               # +0x20 (float)
        'R':              f['o24_R'],               # +0x24 (float)
        'electrodeTecNo': f['o28_electrodeTecNo'],  # +0x28 (1 char)
        'enzymeTecNo':    f['o29_enzymeTecNo'],     # +0x29 (1 char)
        'membraneTecNo':  f['o2a_membraneTecNo'],   # +0x2a (1 char)
    }
    return result


if __name__ == '__main__':
    import json
    tests = [
        # format1 len17:  [A-Z0-9][0-9] (2dig grp) (3dig)(3dig) [0-9]{4} [0-9A-Z]{3}
        "A1050790015234ABC",
        # format1 len18 (the [0-9]{4,5} = 5 digits branch -> strlen 18, K uses 3 digits)
        "A10507900152349ABC",
        # format2 len21: [lead][1][A-Z0-9][0-9](2)(3)(3)[0-9]{6}[0-9A-Z]{3}
        "A21005079001234567ABC",
        # format3 len21: leading 00
        "001005079001234567XYZ",
    ]
    for t in tests:
        r = decode_ct(t)
        print(f"SSN={t!r} (len {len(t)})")
        print("  ", json.dumps(r, ensure_ascii=False))
