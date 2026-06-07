import type { HTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

export interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: ReactNode;
  extra?: ReactNode;
  bodyClass?: string;
  flush?: boolean;
  children: ReactNode;
}

export function Card({ title, extra, bodyClass, flush, children, className, ...rest }: CardProps) {
  return (
    <div className={clsx("idm-card", className)} {...rest}>
      {(title || extra) && (
        <div className="idm-card__header">
          <div className="idm-card__title">{title}</div>
          <div className="idm-card__extra">{extra}</div>
        </div>
      )}
      <div className={clsx(flush ? "idm-card__body--flush" : "idm-card__body", bodyClass)}>
        {children}
      </div>
    </div>
  );
}
