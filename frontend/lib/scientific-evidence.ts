export type ScientificEvidenceRecord = {
  id: string;
  record_type: string;
  status: string;
  schema_version: string;
  experiment_id?: string | null;
  simulation_id: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type TrustDimension = {
  state: string;
  evidence_ids: string[];
  warning?: string;
};

export type ScientificTrustPayload = {
  overall_trust: "HIGH" | "MODERATE" | "LOW" | "INVALID" | string;
  dimensions: Record<string, TrustDimension>;
  reason_code?: string;
  evidence_ids: string[];
  limitations: string[];
  result_hash?: string;
  trust_hash?: string;
};

function asObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function trustPayload(record: ScientificEvidenceRecord | null | undefined): ScientificTrustPayload | null {
  if (!record || record.record_type !== "scientific_trust") return null;
  const payload = asObject(record.payload);
  const dimensions = Object.fromEntries(Object.entries(asObject(payload.dimensions)).map(([name, value]) => {
    const dimension = asObject(value);
    return [name, { state: typeof dimension.state === "string" ? dimension.state : "NOT_RUN", evidence_ids: stringArray(dimension.evidence_ids), warning: typeof dimension.warning === "string" ? dimension.warning : undefined }];
  }));
  return {
    overall_trust: typeof payload.overall_trust === "string" ? payload.overall_trust : "NOT_RETURNED",
    dimensions,
    reason_code: typeof payload.reason_code === "string" ? payload.reason_code : undefined,
    evidence_ids: stringArray(payload.evidence_ids),
    limitations: stringArray(payload.limitations),
    result_hash: typeof payload.result_hash === "string" ? payload.result_hash : undefined,
    trust_hash: typeof payload.trust_hash === "string" ? payload.trust_hash : undefined,
  };
}

export function latestTrustRecord(records: ScientificEvidenceRecord[]): ScientificEvidenceRecord | null {
  return records.filter(record => record.record_type === "scientific_trust").sort((left, right) => (right.created_at || "").localeCompare(left.created_at || ""))[0] || null;
}

export function decisionConfidence(trust: ScientificTrustPayload): "high" | "moderate" | "low" | "invalid" | null {
  const mapping = { HIGH: "high", MODERATE: "moderate", LOW: "low", INVALID: "invalid" } as const;
  return mapping[trust.overall_trust as keyof typeof mapping] || null;
}

export function decisionValidity(trust: ScientificTrustPayload): "valid" | "valid_with_warnings" | "invalid" | null {
  const validity = trust.dimensions.validity;
  if (!validity || validity.state === "NOT_RUN" || !validity.evidence_ids.length) return null;
  if (validity.state === "PASS") return "valid";
  if (validity.state === "WARNING") return "valid_with_warnings";
  if (validity.state === "FAIL") return "invalid";
  return null;
}
