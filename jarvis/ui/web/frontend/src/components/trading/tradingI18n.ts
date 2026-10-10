/**
 * The trading dashboard's own two-language dictionary.
 *
 * The dashboard opens in German and can be switched to English in its header;
 * the choice is a per-viewer convenience kept in localStorage. Both
 * dictionaries ship with the (lazily loaded) section itself, so the switch is
 * instant and nothing loads on app start. `tradingI18n.test.ts` keeps the two
 * key sets identical.
 */
import { create } from "zustand";
import de from "@/i18n/locales/trading/de.json";
import en from "@/i18n/locales/trading/en.json";

export type TradingLanguage = "de" | "en";

export const TRADING_LANGUAGES: readonly TradingLanguage[] = ["de", "en"];
export const DEFAULT_TRADING_LANGUAGE: TradingLanguage = "de";

const STORAGE_KEY = "jarvis.trading.language";
const DICTIONARIES: Record<TradingLanguage, Record<string, unknown>> = { de, en };
const LOCALES: Record<TradingLanguage, string> = { de: "de-DE", en: "en-US" };

function readStored(): TradingLanguage {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "de" || raw === "en") return raw;
  } catch {
    /* private mode / no storage: the default stands */
  }
  return DEFAULT_TRADING_LANGUAGE;
}

interface LanguageState {
  lang: TradingLanguage;
  setLang: (lang: TradingLanguage) => void;
}

export const useTradingLanguageStore = create<LanguageState>((set) => ({
  lang: readStored(),
  setLang: (lang) => {
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* the choice still applies for this session */
    }
    set({ lang });
  },
}));

function lookup(dict: Record<string, unknown>, key: string): string | undefined {
  let node: unknown = dict;
  for (const part of key.split(".")) {
    if (node === null || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string" ? node : undefined;
}

export type TradingT = (key: string, vars?: Record<string, string | number>) => string;

export function translate(
  lang: TradingLanguage,
  key: string,
  vars?: Record<string, string | number>,
): string {
  const template = lookup(DICTIONARIES[lang], key) ?? lookup(DICTIONARIES.en, key) ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

/** Does the dictionary know this key? (Used for codes the backend may add later.) */
export function hasKey(lang: TradingLanguage, key: string): boolean {
  return lookup(DICTIONARIES[lang], key) !== undefined;
}

export function useTradingI18n(): {
  lang: TradingLanguage;
  setLang: (lang: TradingLanguage) => void;
  t: TradingT;
  locale: string;
} {
  const lang = useTradingLanguageStore((s) => s.lang);
  const setLang = useTradingLanguageStore((s) => s.setLang);
  return {
    lang,
    setLang,
    t: (key, vars) => translate(lang, key, vars),
    locale: LOCALES[lang],
  };
}

export function flatKeys(lang: TradingLanguage): string[] {
  const out: string[] = [];
  const walk = (node: unknown, prefix: string) => {
    if (node !== null && typeof node === "object") {
      for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
        walk(v, prefix ? `${prefix}.${k}` : k);
      }
    } else {
      out.push(prefix);
    }
  };
  walk(DICTIONARIES[lang], "");
  return out.sort();
}
