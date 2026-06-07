import type { ReactNode } from "react";
import clsx from "clsx";

export type StatusKind = "ok" | "warn" | "fail" | "idle";

export interface StatusProps {
  kind: StatusKind;
  children: ReactNode;
  className?: string;
}

export function Status({ kind, children, className }: StatusProps) {
  return (
    <span className={clsx("idm-status", `idm-status--${kind}`, className)}>
      <span className="idm-status__dot" />
      {children}
    </span>
  );
}
