import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Navbar } from "./Navbar";


vi.mock("next/image", () => ({
  default: ({ alt, ...props }: { alt: string }) => (
    
    <img alt={alt} {...props} />
  ),
}));

describe("Navbar", () => {
  it("renders the VyapaarOS logo", () => {
    render(<Navbar />);
    const logo = screen.getByAltText("VyapaarOS Logo");
    expect(logo).toBeInTheDocument();
  });

  it("renders the VyapaarOS title", () => {
    render(<Navbar />);
    const title = screen.getByText("VyapaarOS");
    expect(title).toBeInTheDocument();
  });

  it("has transparent header styling", () => {
    const { container } = render(<Navbar />);
    const wrapper = container.firstChild;
    expect(wrapper).toHaveClass("transparent-header");
  });
});
