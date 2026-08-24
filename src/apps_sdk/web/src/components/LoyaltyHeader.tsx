import { ShoppingBag } from "lucide-react";
import { ThemeToggle } from "@/components/ThemeToggle";
import type { MerchantUser } from "@/types";

interface LoyaltyHeaderProps {
  user: MerchantUser;
  cartItemCount?: number;
  onCartClick?: () => void;
}

export function LoyaltyHeader({
  user,
  cartItemCount = 0,
  onCartClick,
}: LoyaltyHeaderProps) {
  return (
    <header className="border-b border-default/50 bg-surface">
      <div className="flex h-16 items-center justify-between px-5">
        {}
        <span className="text-base font-semibold tracking-tight text-text">
          NVShop
        </span>

        {}
        <div className="flex items-center gap-5">
          <div className="flex items-center gap-2.5 text-sm">
            <span className="font-medium text-text">{user.name}</span>

            <span className="text-text-tertiary">•</span>

            <span className="font-medium text-accent">{user.tier}</span>

            <span className="text-text-tertiary">•</span>

            <span className="text-text-secondary">
              {user.loyaltyPoints.toLocaleString()} pts
            </span>
          </div>

          <ThemeToggle />
          {onCartClick && (
            <button
              type="button"
              onClick={onCartClick}
              aria-label={`Shopping cart with ${cartItemCount} items`}
              className="
                relative flex h-9 w-9 items-center justify-center
                rounded-lg border border-default/60
                bg-surface text-text-secondary
                transition-colors
                hover:border-accent/40
                hover:bg-accent/5
                hover:text-accent
                focus:outline-none
                focus:ring-2 focus:ring-accent/30
              "
            >
              <ShoppingBag className="h-[18px] w-[18px]" strokeWidth={1.8} />

              {cartItemCount > 0 && (
                <span
                  className="
                    absolute -right-1.5 -top-1.5
                    flex h-4 min-w-4 items-center justify-center
                    rounded-full bg-accent px-1
                    text-[10px] font-semibold leading-none text-white
                  "
                >
                  {cartItemCount > 9 ? "9+" : cartItemCount}
                </span>
              )}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
