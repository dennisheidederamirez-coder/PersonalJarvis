/**
 * Small pieces shared by the trading dashboard's tabs: the source notice, the
 * warning list, a translated reason with its original journal text, a side
 * badge and a limit meter. Theme tokens only — light and dark mode alike.
 */
import type { ReactNode } from "react";
import { AlertTriangle, Info } from "lucide-react";
import { EmptyRow, StatusDot } from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import type { TradingSource } from "@/types/trading";
import { hasKey, type TradingLanguage, type TradingT } from "./tradingI18n";

/** The one message shown instead of a tab's content when the journal can't be read. */
export function SourceNotice({ source, t }: { source: TradingSource; t: TradingT }) {
  if (source.state === "ok") return null;
  const text = t(`source.${source.state}`, { file: source.file_name, detail: source.detail });
  return (
    <div
      role="status"
      data-testid="trading-source-notice"
      className={cn(
        "flex items-start gap-3 rounded-lg border px-4 py-3 text-sm",
        source.state === "busy"
          ? "border-border bg-card text-muted-foreground"
          : "border-warning/40 bg-warning/10 text-foreground",
      )}
    >
      {source.state === "busy" ? (
        <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      ) : (
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
      )}
      <span>{text}</span>
    </div>
  );
}

export function WarningList({ source, t }: { source: TradingSource; t: TradingT }) {
  if (source.state !== "ok" || source.warnings.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2" data-testid="trading-warnings">
      {source.warnings.map((w) => (
        <li
          key={w}
          className={cn(
            "flex items-start gap-3 rounded-lg border px-4 py-2.5 text-sm",
            w === "kill_switch" || w === "job_refused"
              ? "border-destructive/40 bg-destructive/10"
              : "border-warning/40 bg-warning/10",
          )}
        >
          <AlertTriangle
            className={cn(
              "mt-0.5 h-4 w-4 shrink-0",
              w === "kill_switch" || w === "job_refused" ? "text-destructive" : "text-warning",
            )}
            aria-hidden
          />
          <span>{t(`warning.${w}`)}</span>
        </li>
      ))}
    </ul>
  );
}

/** A reason code as a translated label; the journal's own text underneath. */
export function ReasonText({
  code,
  text,
  lang,
  t,
  signal,
}: {
  code: string;
  text: string;
  lang: TradingLanguage;
  t: TradingT;
  signal?: { code: string | null; text: string | null };
}) {
  const label = hasKey(lang, `reason.${code}`) ? t(`reason.${code}`) : t("reason.other");
  return (
    <div className="min-w-0">
      <div className="truncate text-sm text-foreground">{label}</div>
      {/* the journal's own words, unless they are a bare token like "stop" */}
      {text && code !== "approved" && /\s/.test(text.trim()) ? (
        <div className="truncate font-mono text-xs text-foreground-faint" title={text}>
          {text}
        </div>
      ) : null}
      {signal?.code ? (
        <div className="truncate text-xs text-muted-foreground" title={signal.text ?? undefined}>
          {t("decisions.signal", {
            reason: hasKey(lang, `reason.${signal.code}`)
              ? t(`reason.${signal.code}`)
              : (signal.text ?? ""),
          })}
        </div>
      ) : null}
    </div>
  );
}

export function SideBadge({ side, t }: { side: string | null; t: TradingT }) {
  if (!side) return <span className="text-muted-foreground">—</span>;
  return (
    <span
      className={cn(
        "inline-flex rounded px-1.5 py-0.5 text-xs font-medium",
        side === "long" && "bg-success/15 text-success",
        side === "short" && "bg-destructive/15 text-destructive",
        side !== "long" && side !== "short" && "bg-secondary text-muted-foreground",
      )}
    >
      {t(`side.${side}`)}
    </span>
  );
}

/** How much of a limit is used: a thin bar, green → amber → red. */
export function Meter({ ratio, label }: { ratio: number; label: string }) {
  const clamped = Math.max(0, Math.min(1, ratio));
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-secondary"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
    >
      <div
        className={cn(
          "h-full rounded-full",
          ratio >= 1 ? "bg-destructive" : ratio >= 0.75 ? "bg-warning" : "bg-success",
        )}
        style={{ width: `${clamped * 100}%` }}
      />
    </div>
  );
}

export function Section({
  title,
  children,
  actions,
  testId,
}: {
  title: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  testId?: string;
}) {
  return (
    <section className="flex flex-col gap-3" data-testid={testId}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-foreground-strong">{title}</h3>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <EmptyRow>{children}</EmptyRow>;
}

export function Dot({
  tone,
  label,
}: {
  tone: "ok" | "off" | "warn" | "error" | "busy";
  label: ReactNode;
}) {
  return <StatusDot tone={tone} label={label} />;
}
