import { useCallback } from "react";
import { ShoppingCart, Star } from "lucide-react";
import type { Product } from "@/types";
import { formatPrice, getProductImage } from "@/types";

interface RecommendationCarouselProps {
  products: Product[];
  onAddToCart: (product: Product) => void;
  onProductClick?: (product: Product) => void;
}

interface ProductCardProps {
  product: Product;
  onAddToCart: (product: Product) => void;
  onProductClick?: (product: Product) => void;
}

function ProductCard({ product, onAddToCart, onProductClick }: ProductCardProps) {
  const variantLabel = [product.variant, product.size].filter(Boolean).join(" - ");
  const handleAddToCart = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onAddToCart(product);
    },
    [onAddToCart, product]
  );

  const handleCardClick = useCallback(() => {
    if (onProductClick) {
      onProductClick(product);
    }
  }, [onProductClick, product]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if ((e.key === "Enter" || e.key === " ") && onProductClick) {
        e.preventDefault();
        onProductClick(product);
      }
    },
    [onProductClick, product]
  );

  return (
    <article
      onClick={handleCardClick}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
      aria-label={`View ${product.name} details`}
      className="group flex flex-col cursor-pointer overflow-hidden rounded-lg border border-default bg-surface-elevated transition-all hover:border-accent dark:hover:border-accent/70"
    >
      {}
      <div className="relative aspect-square overflow-hidden">
        <img
          src={getProductImage(product.id)}
          alt={product.name}
          className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-105"
          loading="lazy"
        />
        {}
        <div className="absolute inset-0 bg-black/[0.025] dark:bg-white/[0.025]" />
      </div>

      {}
      <div className="flex flex-1 flex-col gap-0.5 px-2.5 pt-2.5 pb-1.5">
        <h3 className="text-sm font-medium text-text leading-tight line-clamp-2">{product.name}</h3>
        <div className="flex items-center justify-between text-[11px] text-text-secondary">
          <span className="truncate">{variantLabel || "Standard"}</span>
          <span className="flex items-center gap-0.5 flex-shrink-0">
            <Star className="h-2.5 w-2.5 text-amber-500" strokeWidth={2} fill="currentColor" /> 4.8
          </span>
        </div>
        <p className="text-sm font-semibold text-success">
          {formatPrice(product.basePrice)}
        </p>
      </div>

      {}
      <div className="px-2.5 pb-2.5">
        <button
          onClick={handleAddToCart}
          className="flex w-full items-center justify-center gap-1 rounded-full border border-accent/30 bg-transparent px-2 py-1.5 text-xs font-medium text-accent transition-colors hover:border-accent hover:bg-accent/5 active:scale-[0.98] dark:border-accent/40 dark:hover:border-accent/70 dark:hover:bg-accent/10"
          aria-label={`Add ${product.name} to cart`}
        >
          <ShoppingCart className="h-3.5 w-3.5" strokeWidth={2} />
          Add to Cart
        </button>
      </div>
    </article>
  );
}

export function RecommendationCarousel({
  products,
  onAddToCart,
  onProductClick,
}: RecommendationCarouselProps) {
  if (products.length === 0) {
    return null;
  }

  return (
    <section className="py-4">
      <div className="grid grid-cols-2 gap-4">
        {products.map((product) => (
          <ProductCard
            key={product.id}
            product={product}
            onAddToCart={onAddToCart}
            onProductClick={onProductClick}
          />
        ))}
      </div>
    </section>
  );
}
