import React from "react";


export const ThemeProvider = ({
  children,
}: {
  children: React.ReactNode;
  theme?: string;
  density?: string;
  global?: boolean;
  target?: string;
}) => <>{children}</>;


export const Spinner = ({ size = "medium" }: { size?: "small" | "medium" | "large" }) => (
  <div data-testid="nv-spinner" className={`nv-spinner nv-spinner--${size}`} />
);


export const AppBar = ({
  slotLeft,
  slotRight,
  className = "",
}: {
  slotLeft?: React.ReactNode;
  slotRight?: React.ReactNode;
  className?: string;
}) => (
  <header data-testid="nv-appbar" className={`nv-appbar ${className}`}>
    <div data-testid="nv-appbar-left">{slotLeft}</div>
    <div data-testid="nv-appbar-right">{slotRight}</div>
  </header>
);


export const Card = ({
  children,
  className = "",
  interactive,
  onClick,
  slotMedia,
}: {
  children: React.ReactNode;
  className?: string;
  interactive?: boolean;
  onClick?: () => void;
  slotMedia?: React.ReactNode;
}) => (
  <div
    data-testid="nv-card-root"
    className={`nv-card-root ${interactive ? "nv-card-root--interactive" : ""} ${className}`}
    onClick={onClick}
  >
    {slotMedia && (
      <div data-testid="nv-card-media" className="nv-card-media">
        {slotMedia}
      </div>
    )}
    <div data-testid="nv-card-content" className="nv-card-content">
      {children}
    </div>
  </div>
);


export const Text = ({
  children,
  kind,
  className = "",
}: {
  children: React.ReactNode;
  kind?: string;
  className?: string;
}) => (
  <span data-testid="nv-text" className={`nv-text nv-text--${kind} ${className}`}>
    {children}
  </span>
);


export const Button = ({
  children,
  kind,
  color,
  size: _size,
  className = "",
  onClick,
  disabled,
  "aria-label": ariaLabel,
}: {
  children: React.ReactNode;
  kind?: string;
  color?: string;
  size?: string;
  className?: string;
  onClick?: (() => void) | undefined;
  disabled?: boolean;
  "aria-label"?: string;
}) => (
  <button
    data-testid="nv-button"
    className={`nv-button nv-button--${kind} nv-button--${color} ${className}`}
    onClick={onClick}
    disabled={disabled}
    aria-label={ariaLabel}
  >
    {children}
  </button>
);


export const Stack = ({
  children,
  gap,
  align: _align,
  className = "",
}: {
  children: React.ReactNode;
  gap?: string;
  align?: string;
  className?: string;
}) => (
  <div
    data-testid="nv-flex"
    className={`nv-primitive--gap-${gap} nv-flex nv-flex--direction-col ${className}`}
  >
    {children}
  </div>
);


export const Flex = ({
  children,
  gap,
  align,
  justify,
  wrap: _wrap,
  direction,
  className = "",
}: {
  children: React.ReactNode;
  gap?: string;
  align?: string;
  justify?: string;
  wrap?: string;
  direction?: string;
  className?: string;
}) => (
  <div
    data-testid="nv-flex"
    className={`nv-primitive--gap-${gap} nv-flex nv-flex--align-${align} nv-flex--direction-${direction ?? "row"} nv-flex--justify-${justify} ${className}`}
  >
    {children}
  </div>
);


export const Divider = ({ orientation = "horizontal" }: { orientation?: string }) => (
  <div
    data-testid="nv-divider-root"
    className={`nv-divider-root nv-divider-root--orientation-${orientation}`}
  >
    <div
      data-testid="nv-divider-element"
      className={`nv-divider-element nv-divider-element--orientation-${orientation}`}
      role="separator"
    />
  </div>
);


export const Badge = ({
  children,
  kind,
  color,
  className = "",
}: {
  children: React.ReactNode;
  kind?: string;
  color?: string;
  className?: string;
}) => (
  <span
    data-testid="nv-badge"
    className={`nv-badge nv-badge--${kind} nv-badge--${color} ${className}`}
  >
    {children}
  </span>
);


export const Select = ({
  items,
  value,
  onValueChange,
  placeholder,
  size: _size,
  disabled,
}: {
  items: Array<{ value: string; children: string }>;
  value?: string;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  size?: string;
  disabled?: boolean;
}) => (
  <select
    data-testid="nv-select"
    role="combobox"
    value={value}
    onChange={(e) => onValueChange?.(e.target.value)}
    aria-disabled={disabled}
    disabled={disabled}
  >
    {placeholder && (
      <option value="" disabled>
        {placeholder}
      </option>
    )}
    {items.map((item) => (
      <option key={item.value} value={item.value}>
        {item.children}
      </option>
    ))}
  </select>
);
