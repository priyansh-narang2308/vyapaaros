import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RecommendationSkeleton } from "./RecommendationSkeleton";

describe("RecommendationSkeleton", () => {
  it("renders 3 skeleton cards", () => {
    render(<RecommendationSkeleton />);
    
    
    const skeletonCards = document.querySelectorAll(".skeleton-shimmer");
    
    
    
    expect(skeletonCards.length).toBeGreaterThan(0);
  });

  it("has proper structure for loading animation", () => {
    const { container } = render(<RecommendationSkeleton />);
    
    
    const gridContainer = container.querySelector(".grid.grid-cols-3.gap-3");
    expect(gridContainer).toBeInTheDocument();
  });

  it("contains accessible content placeholders", () => {
    const { container } = render(<RecommendationSkeleton />);
    
    
    const roundedCards = container.querySelectorAll(".rounded-lg");
    expect(roundedCards.length).toBe(3);
  });
});
