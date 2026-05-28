import React from "react";

type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "success";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant }) {
  const base =
    "inline-flex items-center justify-center px-4 py-2 rounded-lg font-medium text-sm transition focus:outline-none focus:ring-2 focus:ring-kv-purple/30 disabled:opacity-50 disabled:cursor-not-allowed";
  const styles: Record<BtnVariant, string> = {
    primary: "bg-kv-purple text-white hover:bg-kv-purple-dark shadow-sm",
    secondary:
      "bg-white text-kv-purple-dark border border-kv-purple-300 hover:bg-kv-purple-50",
    ghost: "text-slate-600 hover:text-slate-900 hover:bg-slate-100",
    danger: "bg-red-50 text-red-700 border border-red-200 hover:bg-red-100",
    success: "bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100",
  };
  return <button className={`${base} ${styles[variant]} ${className}`} {...props} />;
}

export function Card({
  className = "",
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`bg-white rounded-2xl border border-slate-200 shadow-sm ${className}`}
    >
      {children}
    </div>
  );
}

export function Input(
  props: React.InputHTMLAttributes<HTMLInputElement> & { label?: string }
) {
  const { label, className = "", ...rest } = props;
  return (
    <label className="block">
      {label && (
        <span className="block text-sm font-medium text-slate-700 mb-1">{label}</span>
      )}
      <input
        className={`w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-kv-purple focus:ring-2 focus:ring-kv-purple/20 outline-none ${className}`}
        {...rest}
      />
    </label>
  );
}

export function Textarea(
  props: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string }
) {
  const { label, className = "", ...rest } = props;
  return (
    <label className="block">
      {label && (
        <span className="block text-sm font-medium text-slate-700 mb-1">{label}</span>
      )}
      <textarea
        className={`w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-kv-purple focus:ring-2 focus:ring-kv-purple/20 outline-none ${className}`}
        {...rest}
      />
    </label>
  );
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "alta" | "media" | "info" | "neutral" | "success" | "warning";
  children: React.ReactNode;
}) {
  const colors: Record<string, string> = {
    alta: "bg-red-50 text-red-700 border-red-200",
    media: "bg-amber-50 text-amber-700 border-amber-200",
    info: "bg-sky-50 text-sky-700 border-sky-200",
    neutral: "bg-slate-50 text-slate-700 border-slate-200",
    success: "bg-emerald-50 text-emerald-700 border-emerald-200",
    warning: "bg-amber-50 text-amber-700 border-amber-200",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border ${colors[tone]}`}
    >
      {children}
    </span>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin h-5 w-5 text-kv-purple ${className}`}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path
        d="M4 12a8 8 0 018-8"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="text-center py-16 px-6">
      <div className="mx-auto h-12 w-12 rounded-full bg-kv-purple-100 flex items-center justify-center mb-4">
        <svg className="h-6 w-6 text-kv-purple" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 4v16m8-8H4" strokeLinecap="round" />
        </svg>
      </div>
      <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
      {description && (
        <p className="mt-1 text-sm text-slate-500 max-w-md mx-auto">{description}</p>
      )}
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}
