"""
Module 2 — Material property library.

Single authoritative in-code source of material properties used by every
Module 2 solver. Mirrors `database/migrations/007_material_library.sql`
(a queryable/auditable reference table with the same values) but this
module is what solvers actually read at request time.

Every property carries its value, unit, source, and (where meaningful) a
valid range and limitation notes - never a bare number. Requesting a
material or property this library does not define raises
`MaterialNotFoundError` / `MaterialPropertyNotFoundError` instead of
silently defaulting or inventing a value.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaterialProperty:
    value: float
    unit: str
    source: str
    valid_range: tuple[float, float] | None = None
    notes: str | None = None


class MaterialNotFoundError(Exception):
    def __init__(self, material_name: str) -> None:
        self.material_name = material_name
        super().__init__(
            f"Material '{material_name}' is not in the material library. "
            "No property values are invented for unknown materials."
        )


class MaterialPropertyNotFoundError(Exception):
    def __init__(self, material_name: str, property_name: str) -> None:
        self.material_name = material_name
        self.property_name = property_name
        super().__init__(
            f"Material '{material_name}' has no known '{property_name}' value in the "
            "material library. This solver cannot run without it - no silent default "
            "is substituted."
        )


# Values are typical/representative engineering-handbook figures for a
# generic member of each material class, not a certified batch/mill-test
# result. See valid_range + notes for the acknowledged variability.
MATERIAL_LIBRARY: dict[str, dict[str, MaterialProperty]] = {
    "concrete": {
        "density": MaterialProperty(2400, "kg/m3", "ASCE/engineering handbook typical value", (2200, 2600)),
        "thermal_conductivity": MaterialProperty(1.7, "W/(m*K)", "ASHRAE Fundamentals typical value", (0.8, 2.0)),
        "elastic_modulus": MaterialProperty(
            30e9, "Pa", "ACI 318 typical value for 30 MPa concrete", (20e9, 40e9),
            "Highly dependent on mix design and age",
        ),
        "poisson_ratio": MaterialProperty(0.2, "dimensionless", "ACI 318 typical value", (0.15, 0.22)),
        "compressive_strength": MaterialProperty(
            30e6, "Pa", "ACI 318 typical normal-strength value", (20e6, 40e6),
            "Brittle material; no defined tensile yield strength",
        ),
    },
    "steel": {
        "density": MaterialProperty(7850, "kg/m3", "ASM Handbook (structural/A36 steel)", (7750, 7950)),
        "thermal_conductivity": MaterialProperty(45.0, "W/(m*K)", "ASM Handbook (carbon steel)", (40.0, 55.0)),
        "elastic_modulus": MaterialProperty(200e9, "Pa", "ASM Handbook (structural steel)", (190e9, 210e9)),
        "yield_strength": MaterialProperty(250e6, "Pa", "ASTM A36 minimum yield strength", (250e6, 400e6)),
        "poisson_ratio": MaterialProperty(0.3, "dimensionless", "ASM Handbook (structural steel)", (0.27, 0.30)),
    },
    "aluminum": {
        "density": MaterialProperty(2700, "kg/m3", "ASM Handbook (6061-T6)", (2650, 2750)),
        "thermal_conductivity": MaterialProperty(205.0, "W/(m*K)", "ASM Handbook (6061-T6)", (150.0, 235.0)),
        "elastic_modulus": MaterialProperty(68.9e9, "Pa", "ASM Handbook (6061-T6)", (68e9, 70e9)),
        "yield_strength": MaterialProperty(276e6, "Pa", "ASM Handbook (6061-T6)", (240e6, 280e6)),
        "poisson_ratio": MaterialProperty(0.33, "dimensionless", "ASM Handbook (6061-T6)", (0.32, 0.35)),
    },
    "granite": {
        "density": MaterialProperty(2700, "kg/m3", "Engineering handbook typical value (igneous rock)", (2600, 2800)),
        "thermal_conductivity": MaterialProperty(2.5, "W/(m*K)", "Engineering handbook typical value", (1.7, 4.0)),
        "elastic_modulus": MaterialProperty(
            50e9, "Pa", "Engineering handbook typical value", (30e9, 70e9),
            "Highly variable by quarry/composition",
        ),
        "poisson_ratio": MaterialProperty(0.25, "dimensionless", "Engineering handbook typical value", (0.2, 0.3)),
        "compressive_strength": MaterialProperty(
            130e6, "Pa", "Engineering handbook typical value", (100e6, 250e6),
            "Brittle material; no defined tensile yield strength",
        ),
    },
    "limestone": {
        "density": MaterialProperty(
            2600, "kg/m3", "Engineering handbook typical value (sedimentary rock)", (2160, 2750)
        ),
        "thermal_conductivity": MaterialProperty(1.3, "W/(m*K)", "Engineering handbook typical value", (1.1, 1.6)),
        "elastic_modulus": MaterialProperty(
            45e9, "Pa", "Engineering handbook typical value", (20e9, 70e9),
            "Highly variable by quarry/composition",
        ),
        "poisson_ratio": MaterialProperty(0.25, "dimensionless", "Engineering handbook typical value", (0.2, 0.3)),
        "compressive_strength": MaterialProperty(
            100e6, "Pa", "Engineering handbook typical value", (60e6, 180e6),
            "Brittle material; no defined tensile yield strength",
        ),
    },
}


def list_materials() -> list[str]:
    return sorted(MATERIAL_LIBRARY.keys())


def get_material(material_name: str) -> dict[str, MaterialProperty]:
    key = material_name.lower().strip()
    if key not in MATERIAL_LIBRARY:
        raise MaterialNotFoundError(material_name)
    return MATERIAL_LIBRARY[key]


def get_property(material_name: str, property_name: str) -> MaterialProperty:
    properties = get_material(material_name)
    if property_name not in properties:
        raise MaterialPropertyNotFoundError(material_name, property_name)
    return properties[property_name]


def properties_as_dict(material_name: str) -> dict[str, dict]:
    """JSON-serializable snapshot of every known property for a material,
    suitable for persisting alongside a simulation result."""
    return {
        name: {
            "value": prop.value,
            "unit": prop.unit,
            "source": prop.source,
            "valid_range": list(prop.valid_range) if prop.valid_range else None,
            "notes": prop.notes,
        }
        for name, prop in get_material(material_name).items()
    }
