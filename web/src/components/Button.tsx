import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "success";
  size?: "sm" | "md" | "lg";
  icon?: boolean;
  loading?: boolean;
  children?: ReactNode;
};

export function Button({ variant = "secondary", size = "md", icon, loading, className, children, disabled, ...rest }: Props) {
  return (
    <button
      type="button"
      className={clsx("btn", `btn--${variant}`, size !== "md" && `btn--${size}`, icon && "btn--icon", className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <span className="spinner" aria-hidden /> : null}
      {children}
    </button>
  );
}
