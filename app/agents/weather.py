import requests

# 固定城市 -> (纬度, 经度)
CITY_COORDS = {
    "周口": (33.63, 114.65),
    "郑州": (34.75, 113.62),
    "北京": (39.90, 116.40),
    "上海": (31.23, 121.47),
    "广州": (23.13, 113.26),
    "南京": (32.06, 118.80),

}

# 户外运动关键词
OUTDOOR_KEYWORDS = [
    "跑步", "晨跑", "夜跑", "骑行", "骑车", "爬山", "登山",
    "篮球", "足球", "网球", "球类", "散步", "快走",
]


def is_outdoor_task(task_text: str) -> bool:
    """任务文本是否户外运动"""
    return any(k in task_text for k in OUTDOOR_KEYWORDS)


def get_weather(lat: float, lon: float, date_str: str) -> dict:
    """
    调用 Open-Meteo 获取某天天气（免费、无需 key）。
    返回 {"date", "temp_max", "temp_min", "precip_prob"}
    只能预报未来 7 天。
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 7,
    }
    resp = requests.get(url, params=params, timeout=10)
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
    """天气是否不宜户外：降水概率>60%，或高温>=35，或低温<=0"""
    return (
        weather["precip_prob"] > 60
        or weather["temp_max"] >= 35
        or weather["temp_min"] <= 0
    )
