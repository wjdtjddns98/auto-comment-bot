import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { cn } from "../../lib/cn";

const controlClasses =
  "block w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-900 " +
  "placeholder:text-gray-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 " +
  "disabled:cursor-not-allowed disabled:bg-gray-100";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(controlClasses, className)} {...props} />;
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(controlClasses, className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(controlClasses, "bg-white", className)} {...props} />;
}

interface FieldProps {
  label: string;
  htmlFor?: string;
  children: ReactNode;
}

export function Field({ label, htmlFor, children }: FieldProps) {
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-1 text-sm text-gray-700">
      <span className="font-medium">{label}</span>
      {children}
    </label>
  );
}
