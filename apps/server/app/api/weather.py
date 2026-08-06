"""天气接口 — 经 open-meteo 免费接口获取数据，无需 API Key，支持 CORS。

open-meteo 为免费、无需 Key 的天气服务；本机网络下可达（已验证）。
流程：① 地理编码（城市名 → 经纬度）→ ② 取实时天气与今日最高/最低温。
前端改为调用本接口，由后端出网获取，绕开浏览器 CORS 与境外网络阻断。
"""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["weather"])

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"

# 内置中国城市坐标表（open-meteo geocoding 未收录的区县级地名）
# 格式：{城市名: (纬度, 经度)}
_CN_CITIES: dict[str, tuple[float, float]] = {
    "文登": (37.196, 122.057),      # 山东威海文登区
    "文登区": (37.196, 122.057),
    "荣成": (37.165, 122.487),      # 山东威海荣成市
    "乳山": (36.920, 121.540),      # 山东威海乳山市
}
_FC_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 12.0

# WMO 天气代码 → (中文描述, emoji)
_WMO: dict[int, tuple[str, str]] = {
    0: ("晴", "☀️"),
    1: ("晴间多云", "🌤"),
    2: ("局部多云", "⛅"),
    3: ("阴", "☁️"),
    45: ("雾", "🌫"),
    48: ("雾凇", "🌫"),
    51: ("毛毛雨", "🌦"),
    53: ("毛毛雨", "🌦"),
    55: ("毛毛雨", "🌦"),
    56: ("冻毛毛雨", "🌧"),
    57: ("冻毛毛雨", "🌧"),
    61: ("小雨", "🌧"),
    63: ("中雨", "🌧"),
    65: ("大雨", "🌧"),
    66: ("冻雨", "🌧"),
    67: ("冻雨", "🌧"),
    71: ("小雪", "🌨"),
    73: ("中雪", "🌨"),
    75: ("大雪", "🌨"),
    77: ("雪粒", "🌨"),
    80: ("阵雨", "🌦"),
    81: ("阵雨", "🌦"),
    82: ("强阵雨", "🌧"),
    85: ("阵雪", "🌨"),
    86: ("强阵雪", "🌨"),
    95: ("雷阵雨", "⛈"),
    96: ("雷阵雨伴冰雹", "⛈"),
    99: ("强雷暴伴冰雹", "⛈"),
}


def _wmo_desc(code: int) -> str:
    return _WMO.get(code, ("未知", "🌤"))[0]


def _num(v) -> str:
    """把温度等数值格式化为整数串；缺失返回 '--'。"""
    if v is None:
        return "--"
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return "--"


@router.get("/weather")
async def get_weather(city: str = Query(..., min_length=1, description="城市名，如 上海")):
    """获取指定城市的实时天气（经 open-meteo 免费接口，无需 Key）。"""
    # ① 内置中国城市坐标表优先（open-meteo 对区县级中文地名覆盖差）
    loc0 = _CN_CITIES.get(city)
    if loc0 is not None:
        lat, lon = loc0
        loc = {"name": city, "latitude": lat, "longitude": lon}
    else:
        # ② 地理编码：城市名 → 经纬度（中国优先 + 名字精确匹配，避免同名城市取错）
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                geo = await client.get(
                    _GEO_URL,
                    params={"name": city, "count": 10, "language": "zh", "format": "json"},
                )
                geo.raise_for_status()
                geo_data = geo.json()
        except Exception as e:
            logger.warning("[weather] 地理编码失败 (%s): %s", city, e)
            raise HTTPException(status_code=502, detail="天气服务暂不可用(地理编码)")

        results = (geo_data or {}).get("results") or []
        if not results:
            raise HTTPException(status_code=404, detail=f"未找到城市: {city}")

        def _is_cn(r: dict) -> bool:
            c = (r.get("country") or "") + str(r.get("country_code") or "")
            return "中国" in c or "China" in c or c.endswith("CN")

        cn = [r for r in results if _is_cn(r)]
        pool = cn or results
        # 名字精确匹配优先（含「区/县/市」后缀）
        names = {city, city + "区", city + "县", city + "市"}
        exact = [r for r in pool if r.get("name") in names]
        loc = (exact or pool)[0]
        lat, lon = loc["latitude"], loc["longitude"]

    # ② 取实时天气 + 今日最高/最低温
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            fc = await client.get(
                _FC_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            fc.raise_for_status()
            fc_data = fc.json()
    except Exception as e:
        logger.warning("[weather] 天气获取失败 (%s): %s", city, e)
        raise HTTPException(status_code=502, detail="天气服务暂不可用")

    cur = fc_data.get("current") or {}
    daily = fc_data.get("daily") or {}
    code = int(cur.get("weather_code", -1))
    return {
        "city": loc.get("name") or city,
        "temp": _num(cur.get("temperature_2m")),
        "type": _wmo_desc(code),
        "high": _num((daily.get("temperature_2m_max") or [None])[0]),
        "low": _num((daily.get("temperature_2m_min") or [None])[0]),
        "wind": _num(cur.get("wind_speed_10m")),
        "humidity": _num(cur.get("relative_humidity_2m")),
    }
