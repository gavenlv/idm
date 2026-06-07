import type { InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  size?: "sm" | "md";
}

export function Input({ size = "md", className, ...rest }: InputProps) {
  return <input className={clsx("idm-input", size === "sm" && "idm-input--sm", className)} {...rest} />;
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  children: ReactNode;
  size?: "sm" | "md";
}

export function Select({ className, children, size = "md", ...rest }: SelectProps) {
  return (
    <select
      className={clsx("idm-select", size === "sm" && "idm-select--sm", className)}
      {...rest}
    >
      {children}
    </select>
  );
}

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {}

export function Textarea({ className, ...rest }: TextareaProps) {
  return <textarea className={clsx("idm-textarea", className)} {...rest} />;
}
