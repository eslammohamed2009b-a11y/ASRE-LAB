"""
Module 1 — Natural language -> DesignParameters via LLM function calling.
"""
import re

import anthropic

from app.core.config import settings
from app.module1_design.schemas import DesignParameters, GeometryType

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None

DESIGN_PARAMETER_TOOL = {
    "name": "extract_design_parameters",
    "description": "Extract structured engineering parameters from a natural-language design request.",
    "input_schema": DesignParameters.model_json_schema(),
}

_NUMBER = r"(\d+(?:\.\d+)?)"
_LENGTH_UNIT = r"(?:m|metre|metres|meter|meters)"


def _first_number(prompt: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _semantic_overrides(prompt: str) -> dict:
    """Extract only explicitly role-labelled values.

    This intentionally does not guess that the first length is a height.  It
    recognises supported grammar around each value and leaves unlabelled input
    to defaults (or the optional language-model convenience parser).
    """
    height = _first_number(prompt, [
        rf"\bheight\s*(?:of\s*)?(?:=|:)?\s*{_NUMBER}\s*{_LENGTH_UNIT}\b",
        rf"\b{_NUMBER}\s*{_LENGTH_UNIT}\s*(?:in\s+height|height|high)\b",
    ])
    base = _first_number(prompt, [
        rf"\bbase(?:\s+length|\s+width)?\s*(?:of\s*)?(?:=|:)?\s*{_NUMBER}\s*{_LENGTH_UNIT}\b",
        rf"\b{_NUMBER}\s*{_LENGTH_UNIT}\s*(?:by|x|Ã—)\s*\d+(?:\.\d+)?\s*{_LENGTH_UNIT}\s+(?:square\s+)?base\b",
        rf"\b{_NUMBER}\s*{_LENGTH_UNIT}\s+(?:square\s+)?base\b",
        rf"\bwidth\s*(?:of\s*)?(?:=|:)?\s*{_NUMBER}\s*{_LENGTH_UNIT}\b",
        rf"\b{_NUMBER}\s*{_LENGTH_UNIT}\s+(?:base\s+)?width\b",
    ])
    slope = _first_number(prompt, [
        rf"\bslope(?:\s+angle)?\s*(?:of\s*)?(?:=|:)?\s*{_NUMBER}\s*(?:degrees?|deg|Â°)\b",
        rf"\b{_NUMBER}\s*(?:degrees?|deg|Â°)\s+(?:face\s+)?slope\b",
    ])
    overrides: dict[str, object] = {}
    if height is not None:
        overrides["height_m"] = height
    if base is not None:
        overrides["base_length_m"] = base
    if slope is not None:
        overrides["slope_angle_deg"] = slope
    for material in ("limestone", "granite", "concrete", "steel", "aluminum"):
        if re.search(rf"\b{material}\b", prompt, flags=re.IGNORECASE):
            overrides["material"] = material
            break
    return overrides


def parse_design_request(natural_language_prompt: str) -> DesignParameters:
    """
    Example input: "A pyramid with a height of 146 meters"
    Returns a validated DesignParameters object; missing fields are
    resolved by DesignParameters.resolve_defaults() via the schema's
    own validator (the "internal knowledge base").
    """
    lower_prompt = natural_language_prompt.lower()
    geometry = GeometryType.PYRAMID
    if "bridge" in lower_prompt:
        geometry = GeometryType.BRIDGE
    elif "tower" in lower_prompt:
        geometry = GeometryType.TOWER
    elif "dome" in lower_prompt:
        geometry = GeometryType.DOME
    elif "arch" in lower_prompt:
        geometry = GeometryType.ARCH

    explicit = _semantic_overrides(natural_language_prompt)
    if client is not None:
        try:
            response = client.messages.create(
                model="claude-sonnet-4-0",
                max_tokens=1024,
                tools=[DESIGN_PARAMETER_TOOL],
                tool_choice={"type": "tool", "name": "extract_design_parameters"},
                messages=[{"role": "user", "content": natural_language_prompt}],
            )
            tool_call = next(b for b in response.content if b.type == "tool_use")
            candidate = dict(tool_call.input)
            candidate["geometry_type"] = geometry
            candidate.update(explicit)
            return DesignParameters(**candidate)
        except Exception:
            # Fallback to deterministic parser when LLM call is unavailable.
            pass

    return DesignParameters(geometry_type=geometry, **explicit)
