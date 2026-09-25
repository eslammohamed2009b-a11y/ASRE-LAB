import { render, screen } from "@testing-library/react";
import HomePage from "@/app/page";

describe("public landing page", () => {
  it("explains the bounded engineering workflow with working destinations", () => {
    render(<HomePage />);
    expect(screen.getByRole("heading", { name: "Design it. Simulate it. Compare it. Understand it." })).toBeInTheDocument();
    expect(screen.getByText("Turn one engineering design into a complete, evidence-backed comparative study.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Start an Engineering Study" })).toHaveLength(3);
    expect(screen.getAllByRole("link", { name: "Start an Engineering Study" })[0]).toHaveAttribute("href", "/auth/sign-up");
    expect(screen.getByRole("link", { name: "See How It Works" })).toHaveAttribute("href", "#workflow");
    expect(screen.getByRole("heading", { name: "What can you actually do with ASRE-Lab?" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "From one idea to a complete engineering study" })).toBeInTheDocument();
    expect(screen.getByText("1 m")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What ASRE-Lab is not" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Scientific Scope" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "Scientific Scope" })[0]).toHaveAttribute("href", "/scientific-scope");
    expect(screen.queryByText(/guaranteed|98\.4%|zero-variance|unlimited for life/i)).not.toBeInTheDocument();
  });
});
