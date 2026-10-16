"""Province handling. China (DXCC 318) uses ISO 3166-2:CN letter codes."""

from __future__ import annotations

import re

from . import callsign

CHINA_DXCC = 318

# code: (English, Chinese, extra aliases)
CN_PROVINCES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "BJ": ("Beijing", "北京", ("京", "peking")),
    "TJ": ("Tianjin", "天津", ("津",)),
    "HE": ("Hebei", "河北", ("冀",)),
    "SX": ("Shanxi", "山西", ("晋",)),
    "NM": ("Inner Mongolia", "内蒙古", ("蒙", "nei mongol", "neimenggu", "nei menggu", "neimongol")),
    "LN": ("Liaoning", "辽宁", ("辽",)),
    "JL": ("Jilin", "吉林", ("吉",)),
    "HL": ("Heilongjiang", "黑龙江", ("黑",)),
    "SH": ("Shanghai", "上海", ("沪",)),
    "JS": ("Jiangsu", "江苏", ("苏",)),
    "ZJ": ("Zhejiang", "浙江", ("浙",)),
    "AH": ("Anhui", "安徽", ("皖",)),
    "FJ": ("Fujian", "福建", ("闽",)),
    "JX": ("Jiangxi", "江西", ("赣",)),
    "SD": ("Shandong", "山东", ("鲁",)),
    "HA": ("Henan", "河南", ("豫",)),
    "HB": ("Hubei", "湖北", ("鄂",)),
    "HN": ("Hunan", "湖南", ("湘",)),
    "GD": ("Guangdong", "广东", ("粤", "canton")),
    "GX": ("Guangxi", "广西", ("桂",)),
    "HI": ("Hainan", "海南", ("琼",)),
    "CQ": ("Chongqing", "重庆", ("渝",)),
    "SC": ("Sichuan", "四川", ("川", "蜀", "szechuan")),
    "GZ": ("Guizhou", "贵州", ("黔", "贵")),
    "YN": ("Yunnan", "云南", ("滇", "云")),
    "XZ": ("Tibet", "西藏", ("藏", "xizang")),
    "SN": ("Shaanxi", "陕西", ("陕", "秦")),
    "GS": ("Gansu", "甘肃", ("甘", "陇")),
    "QH": ("Qinghai", "青海", ("青",)),
    "NX": ("Ningxia", "宁夏", ("宁",)),
    "XJ": ("Xinjiang", "新疆", ("新",)),
}

_SUFFIXES_ZH = ("维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "特别行政区", "省", "市")
_SUFFIXES_EN = ("uygur zizhiqu", "uyghur zizhiqu", "zhuangzu zizhiqu", "huizu zizhiqu", "zizhiqu",
                "autonomous region", "province", "sheng", "shi", "municipality")

_ALIASES: dict[str, str] = {}
for _code, (_en, _zh, _extra) in CN_PROVINCES.items():
    for _a in (_code, _en, _zh, *_extra):
        _ALIASES[_a.lower()] = _code
    _ALIASES[_en.lower().replace(" ", "")] = _code


def normalize_cn(text: str | None) -> str | None:
    """Turn a code, English/pinyin name or Chinese name into an ISO 3166-2:CN code, or None."""
    if not text:
        return None
    t = text.strip()
    if not t:
        return None
    t = re.sub(r"^CN-", "", t, flags=re.I)
    low = t.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    for suf in _SUFFIXES_ZH:
        if t.endswith(suf) and t[: -len(suf)] and t[: -len(suf)].lower() in _ALIASES:
            return _ALIASES[t[: -len(suf)].lower()]
    for suf in _SUFFIXES_EN:
        if low.endswith(" " + suf) or low.endswith(suf):
            stem = low[: -len(suf)].strip()
            if stem in _ALIASES:
                return _ALIASES[stem]
            if stem.replace(" ", "") in _ALIASES:
                return _ALIASES[stem.replace(" ", "")]
    if low.replace(" ", "") in _ALIASES:
        return _ALIASES[low.replace(" ", "")]
    return None


def display_cn(code: str | None, lang: str = "en") -> str:
    if not code or code not in CN_PROVINCES:
        return code or ""
    en, zh, _ = CN_PROVINCES[code]
    return f"{code} · {zh if lang.startswith('zh') else en}"


# Call district → suffix-letter ranges → province (standard allocation; Y–Z are not guessed).
_DISTRICTS: dict[str, list[tuple[str, str, str]]] = {
    "1": [("A", "X", "BJ")],
    "2": [("A", "H", "HL"), ("I", "P", "JL"), ("Q", "X", "LN")],
    "3": [("A", "F", "TJ"), ("G", "L", "NM"), ("M", "R", "HE"), ("S", "X", "SX")],
    "4": [("A", "H", "SH"), ("I", "P", "SD"), ("Q", "X", "JS")],
    "5": [("A", "H", "ZJ"), ("I", "P", "JX"), ("Q", "X", "FJ")],
    "6": [("A", "H", "AH"), ("I", "P", "HA"), ("Q", "X", "HB")],
    "7": [("A", "H", "HN"), ("I", "P", "GD"), ("Q", "X", "GX")],
    "8": [("A", "F", "SC"), ("G", "L", "CQ"), ("M", "R", "GZ"), ("S", "X", "YN")],
    "9": [("A", "F", "SN"), ("G", "L", "GS"), ("M", "R", "NX"), ("S", "X", "QH")],
    "0": [("A", "F", "XJ"), ("G", "L", "XZ")],
}

_CN_CALL = re.compile(r"^B[A-Z]?(\d)([A-Z])")


def guess_cn_province(call: str) -> str | None:
    """Guess the province of a Chinese call from its district digit and first suffix letter."""
    base = callsign.parse(call).base
    m = _CN_CALL.match(base)
    if not m:
        return None
    digit, letter = m.groups()
    for lo, hi, code in _DISTRICTS.get(digit, []):
        if lo <= letter <= hi:
            return code
    return None
