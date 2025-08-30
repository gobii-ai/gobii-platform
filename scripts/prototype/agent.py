#!/usr/bin/env python3
"""
o3 Weather Chat — warning-free version
pip install litellm requests
export OPENAI_API_KEY=...
"""

import os, json, requests, litellm

MODEL = "openrouter/google/gemini-2.5-pro"
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Return current temperature (°C) and weather code for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}]

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def try_geocode(name, country=None):
    r = requests.get(GEOCODE_URL, params={"name": name, "count": 1, "language": "en", "country": country})
    r.raise_for_status()
    hits = r.json().get("results")
    return hits[0] if hits else None


def geocode_smart(city):
    city = city.strip()
    attempts = [city]
    if "," in city:
        base = city.split(",", 1)[0].strip()
        attempts += [base, f"{base} US"]
    tried = set()
    for q in attempts:
        if q in tried:
            continue
        tried.add(q)
        hit = try_geocode(q, country="US")
        if hit:
            return hit["latitude"], hit["longitude"], hit["name"], hit.get("country")
    raise ValueError(f"Could not geolocate '{city}'")


def fetch_weather(lat, lon):
    r = requests.get(FORECAST_URL,
                     params={"latitude": lat, "longitude": lon,
                             "current": "temperature_2m,weather_code",
                             "timezone": "auto"})
    r.raise_for_status()
    return r.json()["current"]


def get_weather(city):
    lat, lon, name, country = geocode_smart(city)
    cur = fetch_weather(lat, lon)
    return json.dumps({"city": name, "country": country,
                       "temperature_c": cur["temperature_2m"],
                       "weather_code": cur["weather_code"]})


def llm(messages, *, tool_choice="auto"):
    return litellm.completion(model=MODEL, messages=messages,
                              tools=TOOLS, tool_choice=tool_choice, timeout=60)


def chat():
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY")
    print("💬  o3 Weather Chat — type 'quit' to exit.")
    history = []

    while True:
        user = input("You: ").strip()
        if user.lower() in {"quit", "exit"}:
            break
        history.append({"role": "user", "content": user})
        resp = llm(history)
        msg = resp.choices[0].message

        while getattr(msg, "tool_calls", None):
            for call in msg.tool_calls:
                args = json.loads(call.function.arguments)
                result = get_weather(**args) if call.function.name == "get_weather" else json.dumps({"error": "unknown"})
                history.extend([
                    msg.model_dump(exclude_none=True),            # 🔧 dict-ified
                    {"role": "tool", "tool_call_id": call.id,
                     "name": call.function.name, "content": result},
                ])
            resp = llm(history)
            msg = resp.choices[0].message

        print(f"o3: {msg.content}")
        history.append({"role": "assistant", "content": msg.content})


if __name__ == "__main__":
    chat()

