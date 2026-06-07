import clsx from "clsx";
import type { ReactNode } from "react";

export interface TagProps {
  color?: string;
  children: ReactNode;
  className?: string;
}

export function Tag({ color = "#999", children, className }: TagProps) {
  return (
    <span className={clsx("idm-tag", className)} style={{ borderColor: color, color }}>
      {children}
    </span>
  );
}
