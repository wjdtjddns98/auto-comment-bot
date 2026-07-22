import { cn } from "../../lib/cn";

export type ToastTone = "success" | "danger";

const toneClasses: Record<ToastTone, string> = {
  success: "bg-tone-success-bg text-tone-success",
  danger: "bg-tone-danger-bg text-tone-danger",
};

interface ToastProps {
  message: string;
  tone?: ToastTone;
  className?: string;
}

export function Toast({ message, tone = "success", className }: ToastProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "fixed bottom-4 right-4 z-50 rounded-md px-4 py-2 text-sm font-medium shadow-lg",
        toneClasses[tone],
        className
      )}
    >
      {message}
    </div>
  );
}
