/**
 * Number and time formatting for the trading dashboard, per dashboard locale.
 * Amounts are the paper accounts' USD; fractions arrive as 0.012 = 1.2 %.
 */

export function money(locale: string, value: number | null | undefined, signed = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(value);
}

export function pct(
  locale: string,
  value: number | null | undefined,
  { signed = false, digits = 2 }: { signed?: boolean; digits?: number } = {},
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(value);
}

export function num(locale: string, value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(value);
}

/** A market price: six significant digits covers BTC (82 578.5) and XRP (1.3949). */
export function price(locale: string, value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, { maximumSignificantDigits: 6 }).format(value);
}

export function qty(locale: string, value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, { maximumSignificantDigits: 5 }).format(value);
}

export function signedR(locale: string, value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "exceptZero",
  }).format(value);
}

export function dateTime(locale: string, ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return new Intl.DateTimeFormat(locale, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ms));
}

export function dayLabel(locale: string, isoDay: string): string {
  const d = new Date(`${isoDay}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return isoDay;
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  }).format(d);
}

/** "vor 12 Minuten" / "in 3 hours" — relative to `now`. */
export function relative(locale: string, ms: number | null | undefined, now: number): string {
  if (ms === null || ms === undefined) return "—";
  const diff = ms - now;
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (abs < 60_000) return rtf.format(Math.round(diff / 1000), "second");
  if (abs < 3_600_000) return rtf.format(Math.round(diff / 60_000), "minute");
  if (abs < 48 * 3_600_000) return rtf.format(Math.round(diff / 3_600_000), "hour");
  return rtf.format(Math.round(diff / 86_400_000), "day");
}

export function bytes(locale: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1024) return `${num(locale, value, 0)} B`;
  if (value < 1024 * 1024) return `${num(locale, value / 1024, 1)} KB`;
  return `${num(locale, value / (1024 * 1024), 1)} MB`;
}

/** Tailwind text class for a signed amount: green up, red down, neutral at zero. */
export function toneClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-foreground";
  return value > 0 ? "text-success" : "text-destructive";
}
