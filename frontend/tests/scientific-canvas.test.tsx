import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ScientificCanvas, type CanvasDesign, type CanvasSimulation } from "@/components/workspace/scientific-canvas";
import { download } from "@/lib/api";

vi.mock("@/lib/api", () => ({ download: vi.fn() }));

const firstDesign: CanvasDesign = { id: "design-1", variation_index: 0, parameters: { base_length_m: 2, height_m: 4, slope_angle_deg: 45, material: "concrete" }, files: [] };
const secondDesign: CanvasDesign = { id: "design-2", variation_index: 1, parameters: { base_length_m: 2, height_m: 5, slope_angle_deg: 50, material: "concrete" }, files: [] };
const firstRun: CanvasSimulation = { id: "run-1", design_id: "design-1", solver_id: "pyramid_thermal_conduction_v1", status: "completed", fields: [], result: { solver_version: "1.0", summary_metrics: { max_temperature_c: 30.41 }, converged: true, warnings: [] } };
const secondRun: CanvasSimulation = { id: "run-2", design_id: "design-2", solver_id: "pyramid_thermal_conduction_v1", status: "completed", fields: [{ id: "field-1", field_name: "temperature" }], result: { solver_version: "1.1", summary_metrics: { max_temperature_c: 31.25, raw_metric: 9 }, converged: false, warnings: ["Reported solver warning"] } };

beforeEach(() => vi.mocked(download).mockReset());

it("shows an honest empty state when a persisted design has no STL", () => {
  render(<ScientificCanvas designs={[firstDesign]} simulations={[firstRun]} solverId="pyramid_thermal_conduction_v1" />);
  expect(screen.getByText("No persisted STL artifact is available for this design.")).toBeInTheDocument();
  expect(screen.getByText(/No STL artifact/)).toBeInTheDocument();
});

it("requests the authenticated persisted STL file for the selected design", async () => {
  const pendingDownload = new Promise<never>(() => {});
  vi.mocked(download).mockReturnValueOnce(pendingDownload);
  render(<ScientificCanvas designs={[{ ...firstDesign, files: [{ id: "stl-1", file_format: "stl" }] }]} simulations={[firstRun]} solverId="pyramid_thermal_conduction_v1" />);
  await waitFor(() => expect(download).toHaveBeenCalledWith("/api/design/files/stl-1/download"));
  expect(screen.getByRole("status")).toHaveTextContent("Loading persisted STL artifact");
});

it("updates matching persisted run context when the selected design changes", () => {
  render(<ScientificCanvas designs={[firstDesign, secondDesign]} simulations={[firstRun, secondRun]} solverId="pyramid_thermal_conduction_v1" />);
  fireEvent.change(screen.getByLabelText("Persisted design"), { target: { value: "design-2" } });
  expect(screen.getByLabelText("Persisted simulation")).toHaveValue("run-2");
  expect(screen.getByText(/Simulation/)).toHaveTextContent("run-2");
  expect(screen.getByText("temperature")).toBeInTheDocument();
});

it("uses the parent-selected persisted run and reports user selection changes", () => {
  const onSimulationChange = vi.fn();
  render(<ScientificCanvas designs={[firstDesign, secondDesign]} simulations={[firstRun, secondRun]} solverId="pyramid_thermal_conduction_v1" selectedDesignId="design-2" selectedSimulationId="run-2" onSimulationChange={onSimulationChange} />);
  expect(screen.getByLabelText("Persisted simulation")).toHaveValue("run-2");
  fireEvent.change(screen.getByLabelText("Persisted simulation"), { target: { value: "run-2" } });
  expect(onSimulationChange).toHaveBeenCalledWith("run-2");
});

it("shows real result metadata without a fabricated spatial field visualization", () => {
  render(<ScientificCanvas designs={[secondDesign]} simulations={[secondRun]} solverId="pyramid_thermal_conduction_v1" />);
  fireEvent.click(screen.getByRole("button", { name: "Result" }));
  expect(screen.getByLabelText("Persisted result context")).toHaveTextContent("run-2");
  expect(screen.getByLabelText("Persisted result context")).toHaveTextContent("Max temperature");
  expect(screen.getByLabelText("Persisted result context")).toHaveTextContent("31.25 °C");
  expect(screen.getByLabelText("Persisted result context")).toHaveTextContent("Solver convergence");
  expect(screen.getByLabelText("Persisted result context")).not.toHaveTextContent("Iterative convergence");
  expect(screen.getByText(/Spatial field visualization is unavailable/)).toBeInTheDocument();
  expect(screen.queryByText(/high trust|validated|contour/i)).not.toBeInTheDocument();
});

it("labels the CAD view and pyramid solver model without calling CAD triangulation a solver mesh", () => {
  render(<ScientificCanvas designs={[firstDesign]} simulations={[firstRun]} solverId="pyramid_thermal_conduction_v1" />);
  expect(screen.getByText("Masked Cartesian finite-difference pyramid model")).toBeInTheDocument();
  expect(screen.getByText(/displayed STL is a design artifact, not the solver mesh/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "CAD triangulation" }));
  expect(screen.getByRole("button", { name: "Solid CAD" })).toBeInTheDocument();
  expect(screen.queryByText(/^solver mesh$/i)).not.toBeInTheDocument();
});
