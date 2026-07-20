import type { ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";
export type ButtonSize = "sm" | "md";

const variantClasses: Record<ButtonVariant, string> = {
  primary: "bg-brand-600 text-white hover:bg-brand-700 focus-visible:outline-brand-600",
  secondary:
    "bg-white text-gray-900 border border-gray-300 hover:bg-gray-50 focus-visible:outline-brand-600",
  danger: "bg-tone-danger text-white hover:opacity-90 focus-visible:outline-tone-danger",
  ghost: "bg-transparent text-gray-700 hover:bg-gray-100 focus-visible:outline-brand-600",
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: "px-2.5 py-1 text-sm",
  md: "px-4 py-2 text-sm",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({ variant = "primary", size = "md", className, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2",
        variantClasses[variant],
        sizeClasses[size],
        className
      )}
      {...props}
    />
  );
}
