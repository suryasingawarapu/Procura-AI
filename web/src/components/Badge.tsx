import clsx from "clsx";
import type { ReactNode } from "react";

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

export function Badge({ tone = "neutral", dot, children, className }: { tone?: Tone; dot?: boolean; children: ReactNode; className?: string }) {
  return <span className={clsx("badge", tone !== "neutral" && `badge--${tone}`, dot && "badge--dot", className)}>{children}</span>;
}
