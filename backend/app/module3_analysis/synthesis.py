"""
Module 3 — Synthesis Engine.
Combines clustering and correlation analysis with an LLM to generate
scientific conclusions and actionable recommendations.
"""
import json

import anthropic

from app.core.config import settings

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None

SYNTHESIS_SYSTEM_PROMPT = """You are the Synthesis Engine of an engineering \
research platform. Given cluster centroids and a correlation matrix from an \
FEA study, produce a JSON object with three keys: "patterns" (list of \
general engineering rules), "trade_offs" (list of competing-objective \
observations), and "recommendations" (list of concrete next Module-1 runs, \
e.g. narrowing a parameter range). Be quantitative and cite the specific \
parameter/metric pairs you were given. Return ONLY the JSON object."""


def synthesize_report(cluster_output: dict, correlation_output: dict) -> dict:
    payload = {
        "clusters": cluster_output["clusters"],
        "top_relationships": correlation_output["top_relationships"],
    }

    if client is not None:
        try:
            response = client.messages.create(
                model="claude-sonnet-4-0",
                max_tokens=1500,
                system=SYNTHESIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            text = "".join(block.text for block in response.content if block.type == "text")
            return json.loads(text)
        except Exception:
            pass

    top_relationships = correlation_output.get("top_relationships", [])
    top = top_relationships[:3]
    return {
        "patterns": [
            "Clustered designs show distinct performance families based on simulation metrics.",
            *[
                f"{r['parameter']} vs {r['metric']} correlation={r['correlation']}"
                for r in top
            ],
        ],
        "trade_offs": [
            "Optimizing one metric can negatively impact another; verify with targeted follow-up runs."
        ],
        "recommendations": [
            "Run a narrowed variation sweep around the top correlated geometric parameters.",
            "Increase sampling density in the highest-performing cluster to refine design rules.",
        ],
    }
