import clsx from "clsx";
import type { ReactNode } from "react";

export interface TagProps {
  color?: string;
  children: ReactNode;
  className?: string;
  solid?: boolean;
  dot?: boolean;
  title?: string;
}

export function Tag({ color = "#697077", children, className, solid, dot, title }: TagProps) {
  return (
    <span
      className={clsx("idm-tag", solid && "idm-tag--solid", className)}
      style={
        solid
          ? { background: color, borderColor: color, color: "#fff" }
          : { borderColor: color, color }
      }
      title={title}
    >
      {dot && <span className="idm-tag__dot" style={{ background: color }} />}
      {children}
    </span>
  );
}
