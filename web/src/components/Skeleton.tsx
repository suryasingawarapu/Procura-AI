import type { CSSProperties } from "react";

export function Skeleton({ w = "100%", h = 16, style }: { w?: number | string; h?: number | string; style?: CSSProperties }) {
  return <div className="skeleton" style={{ width: w, height: h, ...style }} aria-hidden />;
}
