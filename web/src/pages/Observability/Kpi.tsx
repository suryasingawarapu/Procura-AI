import clsx from "clsx";
import { useId } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import type { LucideIcon } from "lucide-react";
import { Skeleton } from "@/components/Skeleton";
import { useChartTheme, useCountUp } from "./hooks";

interface Props {
  label: string;
  value: number;
  format: (n: number) => string;
  icon: LucideIcon;
  tone?: "default" | "danger" | "warning" | "success";
  spark?: number[];
  hint?: string;
}

export function Kpi({ label, value, format, icon: Icon, tone = "default", spark, hint }: Props) {
  const shown = useCountUp(value);
  const theme = useChartTheme();
  const gid = useId().replace(/:/g, "");
  const color =
    tone === "danger" ? "var(--danger)" : tone === "warning" ? "var(--warning)" : tone === "success" ? "var(--success)" : theme.series[0];
  const data = (spark ?? []).map((v, i) => ({ i, v }));
  const hasSpark = data.length > 1 && data.some((d) => d.v > 0);
  return (
    <div className={clsx("obs-kpi card", tone !== "default" && `obs-kpi--${tone}`)}>
      <div className="obs-kpi__top">
        <span className="obs-kpi__label">{label}</span>
        <span className="obs-kpi__icon" aria-hidden>
          <Icon size={15} />
        </span>
      </div>
      <div className="obs-kpi__value" title={format(value)}>
        {format(shown)}
      </div>
      <div className="obs-kpi__foot">
        {hasSpark ? (
          <div className="obs-kpi__spark" aria-hidden>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                    <stop offset="100%" stopColor={color} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.6} fill={`url(#${gid})`} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <span className="obs-kpi__hint">{hint ?? ""}</span>
        )}
      </div>
    </div>
  );
}

export function KpiSkeleton() {
  return (
    <div className="obs-kpi card">
      <Skeleton w={90} h={12} />
      <Skeleton w={120} h={30} style={{ margin: "14px 0 12px" }} />
      <Skeleton h={30} />
    </div>
  );
}
