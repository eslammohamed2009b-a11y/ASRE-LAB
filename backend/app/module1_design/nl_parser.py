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


def parse_design_request(natural_language_prompt: str) -> DesignParameters:
    """
    Example input: "A pyramid with a height of 146 meters"
    Returns a validated DesignParameters object; missing fields are
    resolved by DesignParameters.resolve_defaults() via the schema's
    own validator (the "internal knowledge base").
    """
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
            return DesignParameters(**tool_call.input)
        except Exception:
            # Fallback to deterministic parser when LLM call is unavailable.
            pass

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

    height_match = re.search(r"(\d+(?:\.\d+)?)\s*(m|meter|meters)", lower_prompt)
    height = float(height_match.group(1)) if height_match else None

    return DesignParameters(geometry_type=geometry, height_m=height)
