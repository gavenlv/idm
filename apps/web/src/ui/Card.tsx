import type { HTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
}

export function Card({ title, extra, children, className, ...rest }: CardProps) {
  return (
    <div className={clsx("idm-card", className)} {...rest}>
      {(title || extra) && (
        <div className="idm-card__header">
          <div className="idm-card__title">{title}</div>
          <div className="idm-card__extra">{extra}</div>
        </div>
      )}
      <div className="idm-card__body">{children}</div>
    </div>
  );
}
