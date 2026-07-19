def recommend_analyses(model_type: str) -> list[str]:
    normalized = model_type.lower()
    if "bridge" in normalized:
        return ["structural", "vibration", "thermal"]
    if "pyramid" in normalized:
        return ["thermal", "wind_load"]
    return ["thermal", "structural", "cfd"]
