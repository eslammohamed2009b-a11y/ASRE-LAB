import { render, screen } from "@testing-library/react";
import HomePage from "@/app/page";

describe("public landing page", () => {
  it("uses scientifically bounded product copy and working destinations", () => {
    render(<HomePage />);
    expect(screen.getByRole("heading", { name: /concept to evidence-backed decision/i })).toBeInTheDocument();
    expect(screen.getByText(/Re < 2000/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Scientific Scope" })).toHaveAttribute("href", "/scientific-scope");
    expect(screen.queryByText(/guaranteed|98\.4%|zero-variance|unlimited for life/i)).not.toBeInTheDocument();
  });
});
