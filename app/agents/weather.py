"""天气工具：调用 Open-Meteo 查询天气，判定户外适宜性。

规则（城市、关键词、阈值、接口地址、超时）来自项目根 rules.yaml，
改规则只改 YAML，不改代码。
"""
import requests

from app.core.rules import get_rules

_rules = get_rules()

# 城市经纬度（YAML 里是 [lat, lon]，转成 tuple）
CITY_COORDS = {name: tuple(coord) for name, coord in _rules["cities"].items()}

# 户外运动关键词
OUTDOOR_KEYWORDS = _rules["outdoor_keywords"]


def is_outdoor_task(task_text: str) -> bool:
    """任务文本是否户外运动"""
    return any(k in (task_text or "") for k in OUTDOOR_KEYWORDS)


def get_weather(lat: float, lon: float, date_str: str) -> dict:
    """
    调用 Open-Meteo 获取某天天气（免费、无需 key）。
    返回 {"date", "temp_max", "temp_min", "precip_prob"}
    只能预报未来 7 天。
    """
    w = _rules["weather"]
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": w.get("forecast_days", 7),
    }
    resp = requests.get(w["api_url"], params=params, timeout=w.get("timeout", 10))
    resp.raise_for_status()
    daily = resp.json()["daily"]

    times = daily["time"]
    if date_str not in times:
        raise ValueError(f"天气数据没有 {date_str}，只能预报未来7天")
    i = times.index(date_str)
    return {
        "date": date_str,
        "temp_max": daily["temperature_2m_max"][i],
        "temp_min": daily["temperature_2m_min"][i],
        "precip_prob": daily["precipitation_probability_max"][i],
    }


def is_weather_unfriendly(weather: dict) -> bool:
    """天气是否不宜户外（阈值来自规则库）"""
    t = _rules["weather"]["unfriendly"]
    return (
        weather["precip_prob"] > t["precip_prob_above"]
        or weather["temp_max"] >= t["temp_max_above"]
        or weather["temp_min"] <= t["temp_min_below"]
    )
