import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { EvidenceLedger } from "@/components/workspace/evidence-ledger";
import { ScientificTrustPanel } from "@/components/workspace/scientific-trust-panel";
import { decisionConfidence, decisionValidity, latestTrustRecord, type ScientificEvidenceRecord } from "@/lib/scientific-evidence";

const trust: ScientificEvidenceRecord = { id: "trust-1", record_type: "scientific_trust", status: "complete", schema_version: "1.0", simulation_id: "run-1", created_at: "2026-08-05T00:00:00Z", payload: { overall_trust: "HIGH", dimensions: { validity: { state: "PASS", evidence_ids: ["validity-1"] }, benchmark: { state: "WARNING", evidence_ids: ["benchmark-1"], warning: "bounded benchmark warning" }, run_convergence: { state: "FAIL", evidence_ids: ["run-convergence-1"] }, refinement: { state: "NOT_RUN", evidence_ids: [] } }, reason_code: "BENCHMARK_WARNING", evidence_ids: ["validity-1", "benchmark-1"], limitations: ["Evidence-state classification only"], result_hash: "result-hash", trust_hash: "trust-hash" } };
const notApplicableTrust: ScientificEvidenceRecord = { ...trust, id: "trust-na", payload: { ...trust.payload, overall_trust: "INVALID", dimensions: { validity: { state: "PASS", evidence_ids: ["validity-1"] }, benchmark: { state: "WARNING", evidence_ids: ["benchmark-1"] }, run_convergence: { state: "FAIL", evidence_ids: ["run-convergence-1"] }, refinement: { state: "NOT_APPLICABLE", evidence_ids: [] } } } };

it("renders only persisted evidence and exact Scientific Trust dimensions", () => {
  render(<><ScientificTrustPanel trust={trust} onDerive={vi.fn()} /><ScientificTrustPanel trust={notApplicableTrust} onDerive={vi.fn()} /><EvidenceLedger records={[trust, { ...trust, id: "field-1", record_type: "field_result", payload: { field: "temperature" } }]} /></>);
  expect(screen.getAllByLabelText("Scientific Trust")[0]).toHaveTextContent("Classification HIGH");
  expect(screen.getAllByLabelText("Scientific Trust")[0]).toHaveTextContent(/validity\s*PASS/);
  expect(screen.getAllByLabelText("Scientific Trust")[0]).toHaveTextContent(/run convergence\s*FAIL/);
  expect(screen.getAllByLabelText("Scientific Trust")[0]).toHaveTextContent(/refinement\s*NOT_RUN/);
  expect(screen.getAllByLabelText("Scientific Trust")[1]).toHaveTextContent(/NOT_APPLICABLE/);
  expect(screen.getByLabelText("Evidence ledger")).toHaveTextContent("field_result");
  expect(screen.queryByText(/confidence|percentage|validated/i)).not.toBeInTheDocument();
});

it("offers backend derivation only when the selected run has no persisted trust", () => {
  const onDerive = vi.fn();
  render(<ScientificTrustPanel trust={null} onDerive={onDerive} />);
  fireEvent.click(screen.getByRole("button", { name: "Derive Scientific Trust" }));
  expect(onDerive).toHaveBeenCalledOnce();
});

it("maps trust only through authoritative validity and overall classifications", () => {
  expect(decisionValidity({ overall_trust: "HIGH", dimensions: { validity: { state: "PASS", evidence_ids: ["validity-1"] } }, evidence_ids: [], limitations: [] })).toBe("valid");
  expect(decisionValidity({ overall_trust: "MODERATE", dimensions: { validity: { state: "WARNING", evidence_ids: ["validity-1"] } }, evidence_ids: [], limitations: [] })).toBe("valid_with_warnings");
  expect(decisionValidity({ overall_trust: "LOW", dimensions: { validity: { state: "FAIL", evidence_ids: [] } }, evidence_ids: [], limitations: [] })).toBeNull();
  expect(decisionConfidence({ overall_trust: "INVALID", dimensions: {}, evidence_ids: [], limitations: [] })).toBe("invalid");
  expect(latestTrustRecord([{ ...trust, id: "old", created_at: "2026-01-01T00:00:00Z" }, trust])?.id).toBe("trust-1");
});
