export type Capability = {
  solver_id: string;
  family: string;
  version: string;
  implementation_status: "real" | "prototype" | "planned";
  validation_status: string;
  governing_equations: string[];
  supported_dimensions: string[];
  geometry_limitations: string;
  supported_materials: string[];
  supported_boundary_conditions: string[];
  required_inputs: string[];
  output_metrics: string[];
  known_limitations: string[];
};

export type EvidenceRecord = {
  id: string;
  status: string;
  record_type: string;
  created_at: string;
  payload: Record<string, any>;
};

export type SimulationJob = {
  simulation_id: string;
  experiment_id: string | null;
  design_id: string | null;
  solver_id: string;
  status: "queued" | "running" | "completed" | "partial_failure" | "failed" | "cancelled";
  progress_percent: number;
  error_code: string | null;
  safe_error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type SimulationResults = SimulationJob & {
  result: null | {
    summary_metrics: Record<string, number>;
    assumptions: string[];
    warnings: string[];
    governing_equations: string[];
    convergence: { converged: boolean; iterations: number; residual: number | null; tolerance: number | null };
  };
};

export type Validity = {
  status: "valid" | "valid_with_warnings" | "invalid";
  rules: Array<{
    code: string;
    severity: "warning" | "error";
    affected_input: string;
    message: string;
    suggested_correction: string;
  }>;
  normalized_inputs: Record<string, number>;
};
