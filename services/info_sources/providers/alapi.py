from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional
from urllib.parse import urljoin

try:
    import aiohttp
except Exception:
    aiohttp = None

from services.info_sources.models import InfoSourceResult

RequestFunc = Callable[[str, str, Dict[str, Any], float], Awaitable[Dict[str, Any]]]


class AlapiProvider:
    name = "alapi"
    _CITY_KEYS = ("city", "city_name", "cityname", "area", "location", "province")
    _WEATHER_KEYS = ("weather", "wea", "wea_day", "condition", "text")
    _TEMP_KEYS = ("temperature", "temp", "tem", "temp_float")
    _HUMIDITY_KEYS = ("humidity", "shidu")
    _WIND_DIRECTION_KEYS = ("winddirection", "wind_direction", "wind", "win")
    _WIND_POWER_KEYS = ("windpower", "wind_power", "wind_scale", "win_speed")
    _AIR_KEYS = ("air", "aqi", "air_quality", "quality")
    _FORECAST_LIST_KEYS = (
        "list",
        "forecast",
        "forecasts",
        "future",
        "daily",
        "weather",
        "data",
    )
    _DATE_KEYS = ("date", "day", "ymd", "week")
    _HIGH_TEMP_KEYS = ("tem_day", "temp_high", "high", "max_temp")
    _LOW_TEMP_KEYS = ("tem_night", "temp_low", "low", "min_temp")

    def __init__(
        self,
        endpoint_dir: str | Path,
        token_getter: Callable[[], str],
        request_func: Optional[RequestFunc] = None,
        base_url: str = "https://v3.alapi.cn",
        name: str = "alapi",
        token_param: str = "token",
        timeout_sec: float = 10.0,
        endpoint_configs: Optional[Iterable[Dict[str, Any]]] = None,
    ):
        self.endpoint_dir = Path(endpoint_dir)
        self.token_getter = token_getter
        self.request_func = request_func
        self.name = str(name or "alapi").strip()
        self.base_url = base_url.rstrip("/")
        self.token_param = str(token_param or "token").strip()
        self.timeout_sec = float(timeout_sec)
        self._endpoints = self._load_endpoints()
        for endpoint in endpoint_configs or []:
            capability = str(endpoint.get("id") or "").strip()
            if capability:
                self._endpoints[capability] = dict(endpoint)

    def list_capabilities(self) -> list[str]:
        return sorted(self._endpoints)

    async def fetch(self, capability: str, **params: Any) -> InfoSourceResult:
        endpoint = self._endpoints.get(str(capability or "").strip())
        if not endpoint:
            return InfoSourceResult(
                ok=False,
                provider=self.name,
                capability=capability,
                error=f"unknown alapi capability: {capability}",
            )

        request_params = self._build_params(endpoint, params)
        token = str(self.token_getter() or "").strip()
        if token and self.token_param:
            request_params[self.token_param] = token
        request_params.update(
            {key: value for key, value in params.items() if value is not None}
        )

        method = str(endpoint.get("method") or "GET").strip().upper()
        path = str(endpoint.get("path") or "").strip()
        url = self._build_url(path)
        try:
            raw = await self._request(method, url, request_params)
        except Exception as exc:
            return InfoSourceResult(
                ok=False,
                provider=self.name,
                capability=capability,
                error=str(exc),
            )

        ok = self._is_success(raw)
        data = raw.get("data") if isinstance(raw, dict) else raw
        return InfoSourceResult(
            ok=ok,
            provider=self.name,
            capability=capability,
            data=data,
            summary=self._summary(endpoint, data),
            error="" if ok else self._error(raw),
            raw=raw,
        )

    def has_capability(self, capability: str) -> bool:
        return str(capability or "").strip() in self._endpoints

    def _load_endpoints(self) -> Dict[str, Dict[str, Any]]:
        endpoints: Dict[str, Dict[str, Any]] = {}
        if not self.endpoint_dir.exists():
            return endpoints
        for path in sorted(self.endpoint_dir.glob("*.json")):
            if path.name == "provider.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            capability = str(data.get("id") or path.stem).strip()
            if capability:
                endpoints[capability] = data
        return endpoints

    def _build_params(
        self, endpoint: Dict[str, Any], params: Dict[str, Any]
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        definitions = endpoint.get("params")
        if isinstance(definitions, dict):
            for key, meta in definitions.items():
                if not isinstance(meta, dict):
                    continue
                if "default" in meta:
                    result[str(key)] = meta["default"]
        for key in endpoint.get("default_params") or {}:
            result[str(key)] = endpoint["default_params"][key]
        return result

    def _build_url(self, path: str) -> str:
        if path.lower().startswith(("http://", "https://")):
            return path
        return urljoin(self.base_url + "/", path.lstrip("/"))

    async def _request(
        self, method: str, url: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        if self.request_func is not None:
            return await self.request_func(method, url, params, self.timeout_sec)
        if aiohttp is None:
            raise RuntimeError("aiohttp not installed")
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            request = session.post if method == "POST" else session.get
            async with request(url, params=params) as response:
                response.raise_for_status()
                return await response.json()

    def _is_success(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return payload is not None
        code = payload.get("code")
        if code in (200, "200", 0, "0"):
            return True
        if payload.get("success") is True:
            return True
        return "data" in payload and not payload.get("error")

    def _error(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return "empty response"
        error = payload.get("error") or payload.get("msg") or payload.get("message")
        return str(error or "alapi request failed")

    def _summary(self, endpoint: Dict[str, Any], data: Any) -> str:
        name = str(endpoint.get("name") or endpoint.get("id") or "").strip()
        endpoint_id = str(endpoint.get("id") or "").strip()
        path = str(endpoint.get("path") or "").strip()
        if endpoint_id.startswith("weather_") or "tianqi" in path:
            summary = self._weather_summary(endpoint_id, data)
            if summary:
                return summary
            compact = self._compact_data(data)
            return f"天气数据已返回，但暂时无法解析：{compact}" if compact else ""
        return self._generic_summary(data) or name

    def _weather_summary(self, endpoint_id: str, data: Any) -> str:
        if endpoint_id == "weather_7d":
            forecast = self._forecast_summary(data)
            if forecast:
                return forecast
        return self._current_weather_summary(data)

    def _current_weather_summary(self, data: Any) -> str:
        info = self._find_weather_dict(data)
        if not info:
            return ""
        city = self._first_text(data, self._CITY_KEYS)
        weather = self._first_text(info, self._WEATHER_KEYS)
        temperature = self._first_text(info, self._TEMP_KEYS)
        humidity = self._first_text(info, self._HUMIDITY_KEYS)
        wind_direction = self._first_text(info, self._WIND_DIRECTION_KEYS)
        wind_power = self._first_text(info, self._WIND_POWER_KEYS)
        air = self._first_text(info, self._AIR_KEYS)
        if not any((weather, temperature, humidity, wind_direction, wind_power, air)):
            return ""

        title = f"{city}天气" if city else "天气"
        main = f"{title}：{weather}" if weather else f"{title}已返回"
        details: list[str] = []
        if temperature:
            details.append(self._format_temperature(temperature))
        if humidity:
            details.append(f"湿度{self._with_unit(humidity, '%')}")
        wind = self._format_wind(wind_direction, wind_power)
        if wind:
            details.append(wind)
        if air:
            details.append(f"空气{air}")
        return "，".join([main, *details])

    def _forecast_summary(self, data: Any) -> str:
        items = self._find_forecast_items(data)
        if not items:
            return ""
        city = self._first_text(data, self._CITY_KEYS)
        days: list[str] = []
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            label = self._first_text(item, self._DATE_KEYS)
            weather = self._first_text(item, self._WEATHER_KEYS)
            high = self._first_text(item, self._HIGH_TEMP_KEYS)
            low = self._first_text(item, self._LOW_TEMP_KEYS)
            temperature = self._first_text(item, self._TEMP_KEYS)
            parts = [part for part in (label, weather) if part]
            if high or low:
                parts.append(f"{low or '?'}-{high or '?'}℃")
            elif temperature:
                parts.append(self._format_temperature(temperature))
            if parts:
                days.append(" ".join(parts))
        if not days:
            return ""
        title = f"{city}未来天气" if city else "未来天气"
        return f"{title}：" + "；".join(days)

    def _find_weather_dict(self, data: Any) -> Optional[Dict[str, Any]]:
        for item in self._iter_dicts(data):
            if any(key in item for key in (*self._WEATHER_KEYS, *self._TEMP_KEYS)):
                return item
        return next(self._iter_dicts(data), None)

    def _find_forecast_items(self, data: Any) -> list[Any]:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in self._FORECAST_LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return value
            nested = self._find_forecast_items(value)
            if nested:
                return nested
        return []

    def _iter_dicts(self, data: Any):
        if isinstance(data, dict):
            yield data
            for value in data.values():
                yield from self._iter_dicts(value)
        elif isinstance(data, list):
            for item in data:
                yield from self._iter_dicts(item)

    def _first_text(self, data: Any, keys: Iterable[str]) -> str:
        for item in self._iter_dicts(data):
            for key in keys:
                if key in item:
                    text = self._text(item.get(key))
                    if text:
                        return text
        return ""

    def _text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            for item in value:
                text = self._text(item)
                if text:
                    return text
            return ""
        if isinstance(value, dict):
            for key in ("text", "value", "name"):
                text = self._text(value.get(key))
                if text:
                    return text
            return ""
        text = str(value).strip()
        return "" if text.lower() in {"none", "null"} else text

    def _format_temperature(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if any(unit in text for unit in ("℃", "°", "C", "c")):
            return text
        return f"{text}℃"

    def _with_unit(self, value: str, unit: str) -> str:
        text = str(value or "").strip()
        if not text or text.endswith(unit):
            return text
        return f"{text}{unit}"

    def _format_wind(self, direction: str, power: str) -> str:
        direction = str(direction or "").strip()
        power = str(power or "").strip()
        if direction and "风" not in direction:
            direction = f"{direction}风"
        return f"{direction}{power}".strip()

    def _generic_summary(self, data: Any) -> str:
        compact = self._compact_data(data)
        return compact

    def _compact_data(self, data: Any) -> str:
        if data is None:
            return ""
        if isinstance(data, list):
            if not data:
                return ""
            return json.dumps(data[:3], ensure_ascii=False, separators=(",", ":"))[:300]
        if isinstance(data, dict):
            if not data:
                return ""
            return json.dumps(data, ensure_ascii=False, separators=(",", ":"))[:300]
        return str(data or "").strip()[:300]
