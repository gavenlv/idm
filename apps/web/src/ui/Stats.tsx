import type { ReactNode } from "react";

export interface StatProps {
  label: ReactNode;
  value: ReactNode;
  delta?: ReactNode;
  deltaKind?: "up" | "down";
  hint?: ReactNode;
}

export function Stat({ label, value, delta, deltaKind, hint }: StatProps) {
  return (
    <div className="idm-stat">
      <div className="idm-stat__label">{label}</div>
      <div className="idm-stat__value">{value}</div>
      {delta && (
        <div className={`idm-stat__delta${deltaKind ? ` idm-stat__delta--${deltaKind}` : ""}`}>{delta}</div>
      )}
      {hint && <div className="idm-stat__delta">{hint}</div>}
    </div>
  );
}

export function Stats({ children }: { children: ReactNode }) {
  return <div className="idm-stats">{children}</div>;
}
