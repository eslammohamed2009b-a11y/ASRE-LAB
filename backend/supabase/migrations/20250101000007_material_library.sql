-- ASRE-LAB migration 007: material_library (Module 2 reference material properties)
-- Idempotent. No dependency on other Module 2 tables.
--
-- Global, non-user-owned reference data mirroring
-- `app.module2_simulation.materials.MATERIAL_LIBRARY` (the authoritative
-- in-code source solvers actually read at request time). This table exists
-- as an auditable, queryable record of the same values with their source
-- and valid range, not as a second competing source of truth - the two are
-- expected to be kept in sync manually whenever a material is added.
--
-- Never silently invent missing material data: a material/property not
-- present here (and not in materials.py) must be rejected by the API, not
-- defaulted.

create table if not exists public.material_library (
  id uuid primary key default gen_random_uuid(),
  material_name text not null,
  property_name text not null,
  value double precision not null,
  unit text not null,
  source text not null,
  valid_range_min double precision,
  valid_range_max double precision,
  notes text,
  created_at timestamptz not null default now(),
  unique (material_name, property_name)
);

create index if not exists idx_material_library_name on public.material_library(material_name);

alter table public.material_library enable row level security;

-- Reference data: readable by any authenticated user, not writable through
-- the application (seeded/maintained via migration only).
drop policy if exists material_library_read_all on public.material_library;
create policy material_library_read_all on public.material_library
for select
using (auth.role() = 'authenticated');

insert into public.material_library
  (material_name, property_name, value, unit, source, valid_range_min, valid_range_max, notes)
values
  ('concrete', 'density', 2400, 'kg/m3', 'ASCE/engineering handbook typical value', 2200, 2600, 'Normal-weight structural concrete'),
  ('concrete', 'thermal_conductivity', 1.7, 'W/(m*K)', 'ASHRAE Fundamentals typical value', 0.8, 2.0, null),
  ('concrete', 'elastic_modulus', 30e9, 'Pa', 'ACI 318 typical value for 30 MPa concrete', 20e9, 40e9, 'Highly dependent on mix design and age'),
  ('concrete', 'poisson_ratio', 0.2, 'dimensionless', 'ACI 318 typical value', 0.15, 0.22, null),
  ('concrete', 'compressive_strength', 30e6, 'Pa', 'ACI 318 typical normal-strength value', 20e6, 40e6, 'Brittle material; no defined tensile yield strength'),

  ('steel', 'density', 7850, 'kg/m3', 'ASM Handbook (structural/A36 steel)', 7750, 7950, null),
  ('steel', 'thermal_conductivity', 45.0, 'W/(m*K)', 'ASM Handbook (carbon steel)', 40.0, 55.0, null),
  ('steel', 'elastic_modulus', 200e9, 'Pa', 'ASM Handbook (structural steel)', 190e9, 210e9, null),
  ('steel', 'yield_strength', 250e6, 'Pa', 'ASTM A36 minimum yield strength', 250e6, 400e6, null),
  ('steel', 'poisson_ratio', 0.3, 'dimensionless', 'ASM Handbook (structural steel)', 0.27, 0.30, null),

  ('aluminum', 'density', 2700, 'kg/m3', 'ASM Handbook (6061-T6)', 2650, 2750, null),
  ('aluminum', 'thermal_conductivity', 205.0, 'W/(m*K)', 'ASM Handbook (6061-T6)', 150.0, 235.0, null),
  ('aluminum', 'elastic_modulus', 68.9e9, 'Pa', 'ASM Handbook (6061-T6)', 68e9, 70e9, null),
  ('aluminum', 'yield_strength', 276e6, 'Pa', 'ASM Handbook (6061-T6)', 240e6, 280e6, null),
  ('aluminum', 'poisson_ratio', 0.33, 'dimensionless', 'ASM Handbook (6061-T6)', 0.32, 0.35, null),

  ('granite', 'density', 2700, 'kg/m3', 'Engineering handbook typical value (igneous rock)', 2600, 2800, null),
  ('granite', 'thermal_conductivity', 2.5, 'W/(m*K)', 'Engineering handbook typical value', 1.7, 4.0, null),
  ('granite', 'elastic_modulus', 50e9, 'Pa', 'Engineering handbook typical value', 30e9, 70e9, 'Highly variable by quarry/composition'),
  ('granite', 'poisson_ratio', 0.25, 'dimensionless', 'Engineering handbook typical value', 0.2, 0.3, null),
  ('granite', 'compressive_strength', 130e6, 'Pa', 'Engineering handbook typical value', 100e6, 250e6, 'Brittle material; no defined tensile yield strength'),

  ('limestone', 'density', 2600, 'kg/m3', 'Engineering handbook typical value (sedimentary rock)', 2160, 2750, null),
  ('limestone', 'thermal_conductivity', 1.3, 'W/(m*K)', 'Engineering handbook typical value', 1.1, 1.6, null),
  ('limestone', 'elastic_modulus', 45e9, 'Pa', 'Engineering handbook typical value', 20e9, 70e9, 'Highly variable by quarry/composition'),
  ('limestone', 'poisson_ratio', 0.25, 'dimensionless', 'Engineering handbook typical value', 0.2, 0.3, null),
  ('limestone', 'compressive_strength', 100e6, 'Pa', 'Engineering handbook typical value', 60e6, 180e6, 'Brittle material; no defined tensile yield strength')
on conflict (material_name, property_name) do nothing;
