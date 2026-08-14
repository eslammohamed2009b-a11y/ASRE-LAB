from __future__ import annotations

from time import perf_counter

import pytest

from app.module1_design.cad_v2_compiler import compile_design, export_compiled_design
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2, ValidationStatus
from app.module2_simulation.geometry_physics_schemas import MeshSpecification, PhysicsDomain
from app.module2_simulation.meshing import generate_mesh

pytestmark = pytest.mark.integration


def q(value: float, unit: str = "mm") -> dict:
    return {"value": value, "unit": unit}


def v3(x: float = 0, y: float = 0, z: float = 0) -> dict:
    return {"x": q(x), "y": q(y), "z": q(z)}


def _enclosure() -> EngineeringDesignDocumentV2:
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": "reference_enclosure",
        "parameters": [
            {"name": "case_width", "parameter_type": "length", "value": 80, "unit": "mm", "minimum": 60, "maximum": 120, "design_variable": True},
            {"name": "case_depth", "parameter_type": "length", "value": 30, "unit": "mm", "minimum": 20, "maximum": 50, "design_variable": True},
            {"name": "wall", "parameter_type": "length", "value": 2, "unit": "mm", "minimum": 1, "maximum": 4, "design_variable": True},
        ],
        "datum_planes": [{"datum_id": "lid_plane", "origin": {"x": q(0), "y": q(0), "z": {"parameter": "case_depth"}}}],
        "bodies": [
            {"body_id": "blank", "material": "ABS"},
            {"body_id": "enclosure", "material": "ABS"},
            {"body_id": "lid", "material": "ABS"},
            {"body_id": "standoff", "material": "ABS"},
            {"body_id": "standoffs", "material": "ABS"},
        ],
        "sketches": [
            {"sketch_id": "case", "entities": [
                {"entity_type": "rectangle", "entity_id": "outline", "width": {"parameter": "case_width"}, "height": q(50)}
            ]},
            {"sketch_id": "lid_profile", "plane": "lid_plane", "entities": [
                {"entity_type": "rectangle", "entity_id": "lid_outline", "width": {"parameter": "case_width"}, "height": q(50)}
            ]},
            {"sketch_id": "standoff_profile", "entities": [
                {"entity_type": "circle", "entity_id": "boss", "radius": q(3), "center": {"x": q(-20), "y": q(-10)}}
            ]},
        ],
        "features": [
            {"operation": "extrude", "feature_id": "case_blank", "sketch_id": "case", "output_body": "blank", "distance": {"parameter": "case_depth"}, "taper_angle": q(1, "deg")},
            {"operation": "shell", "feature_id": "case_shell", "source_body": "blank", "output_body": "enclosure", "thickness": {"parameter": "wall"}, "remove_faces": "max_z"},
            {"operation": "extrude", "feature_id": "make_lid", "sketch_id": "lid_profile", "output_body": "lid", "distance": q(2)},
            {"operation": "extrude", "feature_id": "make_standoff", "sketch_id": "standoff_profile", "output_body": "standoff", "distance": q(8)},
            {"operation": "grid_pattern", "feature_id": "repeat_standoffs", "source_body": "standoff", "output_body": "standoffs", "x_spacing": q(40), "y_spacing": q(20), "x_count": 2, "y_count": 2},
        ],
        "output_body_ids": ["enclosure", "lid", "standoffs"],
        "semantic_regions": [
            {"tag": "mounting_floor", "body_id": "enclosure", "selector": {"selector_type": "extreme_face", "axis": "z", "extreme": "minimum"}},
            {"tag": "open_rim", "body_id": "enclosure", "selector": "all_edges"},
            {"tag": "lid_outer", "body_id": "lid", "source_feature_id": "make_lid", "selector": {"selector_type": "extreme_face", "axis": "z", "extreme": "maximum"}},
        ],
        "engineering_interfaces": [{"interface_id": "case_support", "region_tag": "mounting_floor", "role": "structural_support_candidate", "compatible_physics": ["structural"]}],
    })


def _manifold() -> EngineeringDesignDocumentV2:
    features = [{"operation": "extrude", "feature_id": "manifold_blank", "sketch_id": "block", "output_body": "blank", "distance": q(25)}]
    bodies = [{"body_id": "blank"}]
    source = "blank"
    for index, x in enumerate((-25, 0, 25), start=1):
        output = f"drilled_{index}"
        bodies.append({"body_id": output})
        features.append({
            "operation": "hole", "feature_id": f"port_{index}", "source_body": source,
            "output_body": output, "hole_type": "counterbore" if index == 2 else "through",
            "center": v3(x=x, z=25), "axis_direction": [0, 0, -1], "diameter": q(8),
            **({"depth": q(25), "counterbore_diameter": q(14), "counterbore_depth": q(5)} if index == 2 else {}),
        })
        source = output
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": "reference_manifold",
        "parameters": [{"name": "manifold_width", "parameter_type": "length", "value": 80, "unit": "mm", "minimum": 70, "maximum": 120, "design_variable": True}],
        "bodies": bodies,
        "sketches": [{"sketch_id": "block", "entities": [
            {"entity_type": "rectangle", "entity_id": "outline", "width": {"parameter": "manifold_width"}, "height": q(35)}
        ]}],
        "features": features,
        "output_body_ids": [source],
        "semantic_regions": [{"tag": "fluid_ports", "body_id": source, "selector": {"selector_type": "cylindrical_radius", "radius": q(4), "allow_multiple": True}}],
        "engineering_interfaces": [{"interface_id": "port_interface", "region_tag": "fluid_ports", "role": "flow_inlet_candidate", "compatible_physics": ["fluid"]}],
    })


def _assembly() -> EngineeringDesignDocumentV2:
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": "reference_assembly",
        "parameters": [{"name": "rail_width", "parameter_type": "length", "value": 60, "unit": "mm", "minimum": 50, "maximum": 100, "design_variable": True}],
        "bodies": [{"body_id": "rail"}, {"body_id": "pin"}],
        "sketches": [
            {"sketch_id": "rail_profile", "entities": [{"entity_type": "rectangle", "entity_id": "rail_rect", "width": {"parameter": "rail_width"}, "height": q(10)}]},
            {"sketch_id": "pin_profile", "entities": [{"entity_type": "circle", "entity_id": "pin_circle", "radius": q(3)}]},
        ],
        "features": [
            {"operation": "extrude", "feature_id": "make_rail", "sketch_id": "rail_profile", "output_body": "rail", "distance": q(5)},
            {"operation": "extrude", "feature_id": "make_pin", "sketch_id": "pin_profile", "output_body": "pin", "distance": q(15)},
        ],
        "output_body_ids": ["rail", "pin"],
        "components": [
            {"component_id": "rail_component", "name": "Rail", "body_ids": ["rail"], "material": "Steel", "interface_ids": ["rail_support"]},
            {"component_id": "pin_component", "name": "Pin", "body_ids": ["pin"], "material": "Steel"},
        ],
        "component_instances": [
            {"instance_id": "rail_instance", "component_id": "rail_component"},
            {"instance_id": "pin_left", "component_id": "pin_component", "placement": {"translation": v3(x=-15, z=5)}},
            {"instance_id": "pin_right", "component_id": "pin_component", "repeated_from_instance_id": "pin_left", "placement": {"translation": v3(x=15, z=5)}},
        ],
        "assembly_relationships": [
            {"relationship_id": "left_offset", "relationship_type": "offset", "first_instance_id": "rail_instance", "second_instance_id": "pin_left", "offset": q(15)},
            {"relationship_id": "right_offset", "relationship_type": "offset", "first_instance_id": "rail_instance", "second_instance_id": "pin_right", "offset": q(15)},
        ],
        "detect_interference": True,
        "semantic_regions": [{"tag": "rail_mount", "body_id": "rail", "source_feature_id": "make_rail", "selector": {"selector_type": "extreme_face", "axis": "z", "extreme": "minimum"}}],
        "engineering_interfaces": [{"interface_id": "rail_support", "region_tag": "rail_mount", "role": "contact_interface", "compatible_physics": ["contact", "structural"]}],
    })


@pytest.mark.parametrize("name,builder", [
    ("enclosure", _enclosure),
    ("manifold", _manifold),
    ("assembly", _assembly),
])
def test_advanced_reference_models_compile_validate_and_export(name, builder, tmp_path):
    started = perf_counter()
    compiled = compile_design(builder())
    artifacts = export_compiled_design(compiled, tmp_path / name)
    elapsed = perf_counter() - started
    assert compiled.validation.status == ValidationStatus.VALID
    assert {item.metadata.file_format for item in artifacts} == {"step", "stl"}
    assert all(item.path.stat().st_size > 0 for item in artifacts)
    assert compiled.semantic_regions
    changed_payload = builder().model_dump(mode="json")
    changed_payload["parameters"][0]["value"] += 5
    changed = compile_design(EngineeringDesignDocumentV2.model_validate(changed_payload))
    assert changed.design_hash != compiled.design_hash
    assert all(item["status"] != "LOST" for item in changed.semantic_regions)
    assert elapsed < 20


def test_reference_enclosure_authoritative_brep_meshes_and_maps_mounting_floor():
    compiled = compile_design(_enclosure())
    mesh = generate_mesh(
        compiled,
        [PhysicsDomain(domain_id="enclosure_solid", source_body_id="enclosure", domain_kind="solid")],
        MeshSpecification(target_size=q(15)),
    )
    assert mesh.metadata.design_hash == compiled.design_hash
    mapping = next(item for item in mesh.metadata.semantic_mappings if item.semantic_region == "mounting_floor")
    assert mapping.boundary_facet_ids
    assert "open_rim" in " ".join(mesh.metadata.warnings)


def test_reference_manifold_authoritative_brep_meshes_and_maps_cylindrical_ports():
    compiled = compile_design(_manifold())
    body_id = compiled.document.output_body_ids[0]
    mesh = generate_mesh(
        compiled,
        [PhysicsDomain(domain_id="manifold_solid", source_body_id=body_id, domain_kind="solid")],
        MeshSpecification(target_size=q(15), semantic_sizing=[{
            "semantic_region": "fluid_ports", "target_size": q(7.5),
        }]),
    )
    ports = next(item for item in mesh.metadata.semantic_mappings if item.semantic_region == "fluid_ports")
    assert ports.boundary_facet_ids
    assert ports.cad_resolution_status == "RESELECTED"


def test_reference_multi_body_mesh_retains_independent_domain_identity():
    compiled = compile_design(_assembly())
    mesh = generate_mesh(
        compiled,
        [
            PhysicsDomain(domain_id="rail_domain", source_body_id="rail", domain_kind="solid"),
            PhysicsDomain(domain_id="pin_domain", source_body_id="pin", domain_kind="solid"),
        ],
        MeshSpecification(target_size=q(10)),
    )
    assert {item.domain_id for item in mesh.metadata.domains} == {"rail_domain", "pin_domain"}
    assert all(item.volume_element_ids for item in mesh.metadata.domains)
    mount = next(item for item in mesh.metadata.semantic_mappings if item.semantic_region == "rail_mount")
    assert mount.domain_ids == ["rail_domain"]
