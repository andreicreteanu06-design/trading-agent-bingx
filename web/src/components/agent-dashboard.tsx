"use client";

/**
 * Consola Agent BingX. Construita dupa web/DESIGN.md.
 *
 * Ordinea sectiunilor e dupa valoare pentru operator (§6), nu dupa spectacol:
 * rail sticky cu starea de risc, apoi semnalele, apoi instrumentele, apoi
 * jurnalul, apoi backtest-ul cu verdictul lui negativ pastrat vizibil.
 *
 * Bugetul de miscare merge pe date, nu pe chrome (§3). Se animeaza doar ce
 * s-a schimbat: o cifra, un scan care ruleaza, un semnal nou intrat.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  Clock,
  Radio,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  Wifi,
  XCircle,
} from "lucide-react";

import { AnimateDigits } from "@/components/ui/animate-digits";
import { ExecLog, type LogLine } from "@/components/ui/exec-log";
import { OutcomeDonut, type OutcomeSlice } from "@/components/ui/outcome-donut";
import { PaperBookSection } from "@/components/paper-book";
import { RiskEnvelope } from "@/components/risk-envelope";
import { ScoreChart, type ScorePoint } from "@/components/ui/score-chart";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/* tipuri (neschimbate fata de API-ul Python)                          */
/* ------------------------------------------------------------------ */

type Status = {
  scanning: boolean;
  auto_enabled: boolean;
  auto_interval_min: number;
  last_error: string;
  symbols: string[];
  invalid_symbols: string[];
  claude_enabled: boolean;
  telegram_enabled: boolean;
  bingx_authenticated: boolean;
  equity: number | null;
  kill_switch: {
    allowed: boolean;
    reason: string;
    status_line: string;
    trades_today: number;
    max_trades: number;
    consecutive_losses: number;
    max_consecutive: number;
    pnl_today: number;
    peak_equity: number;
    /** fractii, ex. 0.15 = 15%. absente pe un server vechi */
    max_drawdown?: number;
    max_daily_loss?: number;
  };
  blackout: { clear: boolean; reasons: string[]; until: string };
  risk: {
    risk_per_trade: number;
    max_leverage: number;
    max_open_positions: number;
    min_risk_reward: number;
    htf: string;
    ltf: string;
  };
  server_time: string;
};

type SignalData = {
  symbol: string;
  side: "long" | "short";
  entry: number;
  stop_loss: number;
  take_profits: number[];
  score: number;
  reasons: string[];
  warnings: string[];
  risk_reward: number;
  stop_distance_pct: number;
};

type TradeData = {
  approved: boolean;
  symbol: string;
  side: "long" | "short";
  entry: number;
  stop_loss: number;
  take_profits: number[];
  position_size: number;
  notional: number;
  leverage: number;
  risk_amount: number;
  risk_pct: number;
  liquidation_price: number | null;
  liquidation_buffer_mult: number | null;
  rejections: string[];
  notes: string[];
};

type AnalysisData = {
  verdict?: string;
  confidence?: number;
  reasoning?: string;
  key_risks?: string[];
  invalidation?: string;
};

type SymbolResult = {
  symbol: string;
  status:
    | "approved"
    | "rejected"
    | "no_setup"
    | "skipped"
    | "error"
    | "claude_skip";
  detail: string;
  signal: SignalData | null;
  trade: TradeData | null;
  analysis: AnalysisData | null;
};

type ScanResult = {
  started_at?: string;
  finished_at?: string;
  equity?: number | null;
  equity_is_real?: boolean;
  open_positions?: Record<string, unknown>[];
  kill_switch_ok?: boolean;
  kill_switch_status?: string;
  kill_switch_reason?: string;
  blackout_ok?: boolean;
  blackout_reasons?: string[];
  blackout_until?: string;
  error?: string;
  results?: SymbolResult[];
};

type HistoryRecord = {
  ts: string;
  status: string;
  signal?: SignalData;
  trade?: TradeData;
  analysis?: AnalysisData | null;
};

type BacktestReport = {
  symbol: string;
  total_trades: number;
  win_rate: number;
  expectancy_r: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  period_start: string;
  period_end: string;
};

const order: Record<SymbolResult["status"], number> = {
  approved: 0,
  claude_skip: 1,
  rejected: 2,
  error: 3,
  skipped: 4,
  no_setup: 5,
};

/* ------------------------------------------------------------------ */
/* formatare. readout de instrument: fara valori lipsa mascate         */
/* ------------------------------------------------------------------ */

/** null readout, ca pe un instrument fizic */
export const NIL = "--";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export function fmt(n: number | null | undefined, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return NIL;
  return new Intl.NumberFormat("ro-RO", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
}

export function fmtPrice(n: number | null | undefined) {
  if (n === null || n === undefined || Number.isNaN(n)) return NIL;
  const abs = Math.abs(n);
  const digits = abs >= 1000 ? 1 : abs >= 10 ? 2 : abs >= 1 ? 3 : 5;
  return fmt(n, digits);
}

export function localTime(iso?: string) {
  if (!iso) return NIL;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NIL;
  return d.toLocaleString("ro-RO", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ------------------------------------------------------------------ */
/* primitive de material                                               */
/* ------------------------------------------------------------------ */

/**
 * Un panou e o piesa de carcasa (§12): muchia de sus prinde lumina, restul sta.
 *
 * Nu are varianta "interactiva". Varianta veche ridica panoul si plimba un
 * shimmer dupa cursor, ceea ce incalca §3: bugetul de miscare merge pe date,
 * nu pe chrome. Pe deasupra, apela hook-uri conditionat (`if (interactive)`),
 * ceea ce e o incalcare a regulilor hook-urilor si se strica la prima
 * schimbare de `prefers-reduced-motion`.
 */
export function Panel({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("panel relative", className)}>
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-white/10" />
      {children}
    </section>
  );
}

export function Label({ children }: { children: React.ReactNode }) {
  return <span className="label">{children}</span>;
}

/** celula de citire: eticheta mica, valoare mono */
export function Readout({
  label,
  value,
  tone = "hi",
  live = false,
}: {
  label: string;
  value: string;
  tone?: "hi" | "mid" | "long" | "short" | "warn";
  live?: boolean;
}) {
  const color =
    tone === "long"
      ? "text-long"
      : tone === "short"
        ? "text-short"
        : tone === "warn"
          ? "text-warn"
          : tone === "mid"
            ? "text-mid"
            : "text-hi";
  return (
    <div className="cell px-3 py-2.5">
      <Label>{label}</Label>
      <p className={cn("num mt-1.5 font-mono text-[13px] tabular-nums", color)}>
        {live ? <AnimateDigits value={value} /> : value}
      </p>
    </div>
  );
}

/** Citire mare care se aprinde scurt cand valoarea chiar s-a schimbat (§9). */
export function LiveMetric({
  label,
  value,
  helper,
  tone,
}: {
  label: string;
  value: string;
  helper: string;
  tone?: "hi" | "long" | "short";
}) {
  const previous = useRef(value);
  const [flash, setFlash] = useState<"up" | "down" | undefined>();
  const reduceMotion = useReducedMotion();

  // flash on data change
  useEffect(() => {
    if (previous.current === value || previous.current === NIL) {
      previous.current = value;
      return;
    }

    const oldNumber = Number(previous.current.replace(/[^\d,-]/g, "").replace(",", "."));
    const newNumber = Number(value.replace(/[^\d,-]/g, "").replace(",", "."));

    if (Number.isFinite(oldNumber) && Number.isFinite(newNumber)) {
      setFlash(newNumber >= oldNumber ? "up" : "down");
      const t = window.setTimeout(() => setFlash(undefined), 360);
      previous.current = value;
      return () => window.clearTimeout(t);
    }

    previous.current = value;
  }, [value]);

  // Nicio respiratie de fundal. O celula care pulseaza non-stop e miscare
  // decorativa pe chrome (§3, §9) si arde un rAF permanent pe o pagina care sta
  // deschisa toata ziua. Singura miscare permisa aici e flash-ul de la §9: se
  // aprinde doar cand cifra chiar s-a schimbat.

  return (
    <motion.div
      className="cell relative overflow-hidden px-3 py-2.5"
      data-flash={flash}
      animate={flash && !reduceMotion ? { scale: [1, 1.018, 1] } : undefined}
      transition={{ duration: 0.22, ease: EASE_SNAP }}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-white/10" />
      <Label>{label}</Label>
      <p
        className={cn(
          "num mt-1.5 font-mono text-[18px] tabular-nums",
          tone === "long"
            ? "text-long"
            : tone === "short"
              ? "text-short"
              : "text-hi",
        )}
      >
        <AnimateDigits value={value} />
      </p>
      <p className="mt-1 text-[11px] text-lo">{helper}</p>
    </motion.div>
  );
}

function StatusBadge({ status }: { status: SymbolResult["status"] | string }) {
  const map: Record<string, { label: string; className: string }> = {
    approved: { label: "aprobat", className: "text-long border-long/30" },
    rejected: { label: "respins", className: "text-short border-short/30" },
    claude_skip: { label: "claude skip", className: "text-warn border-warn/30" },
    no_setup: { label: "fara setup", className: "text-lo border-line" },
    skipped: { label: "sarit", className: "text-lo border-line" },
    error: { label: "eroare", className: "text-short border-short/30" },
  };
  const item = map[status] ?? map.no_setup;
  return (
    <span
      className={cn(
        "rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em]",
        item.className,
      )}
    >
      {item.label}
    </span>
  );
}

function SidePill({ side }: { side: "long" | "short" }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em]",
        side === "long"
          ? "border-long/30 text-long"
          : "border-short/30 text-short",
      )}
    >
      {side}
    </span>
  );
}

export function Notice({
  tone,
  title,
  text,
}: {
  tone: "short" | "warn";
  title: string;
  text: string;
}) {
  const Icon = tone === "short" ? XCircle : AlertTriangle;
  return (
    <div
      className={cn(
        "flex gap-3 rounded-cell border p-3.5",
        tone === "short"
          ? "border-short/30 bg-short/[0.06]"
          : "border-warn/30 bg-warn/[0.06]",
      )}
    >
      <Icon
        strokeWidth={1.5}
        className={cn(
          "mt-0.5 h-4 w-4 shrink-0",
          tone === "short" ? "text-short" : "text-warn",
        )}
      />
      <div className="min-w-0">
        <p
          className={cn(
            "text-[13px] font-medium",
            tone === "short" ? "text-short" : "text-warn",
          )}
        >
          {title}
        </p>
        <p className="mt-1 text-[13px] leading-relaxed text-mid">{text}</p>
      </div>
    </div>
  );
}

export function Empty({
  icon: Icon,
  text,
}: {
  icon: typeof Clock;
  text: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-cell border border-dashed border-line px-6 py-10 text-center">
      <Icon strokeWidth={1.5} className="mb-3 h-5 w-5 text-lo" />
      <p className="max-w-sm text-[13px] text-mid">{text}</p>
    </div>
  );
}

export function SectionHead({
  title,
  meta,
}: {
  title: string;
  meta?: string;
}) {
  return (
    <div className="mt-10 mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h2 className="text-[15px] font-medium tracking-tight text-hi">{title}</h2>
      {meta && <p className="text-[13px] text-lo">{meta}</p>}
    </div>
  );
}

function TelemetryRail({
  symbols,
  results,
  scanning,
}: {
  symbols: string[];
  results: SymbolResult[];
  scanning: boolean;
}) {
  const bySymbol = new Map(results.map((r) => [r.symbol, r.status]));

  if (!symbols.length) return null;

  return (
    <div
      className="hidden min-w-[220px] items-center gap-1.5 lg:flex"
      aria-label="telemetrie simboluri"
    >
      {symbols.slice(0, 12).map((symbol, i) => {
        const status = bySymbol.get(symbol);
        const tone =
          status === "approved"
            ? "bg-long"
            : status === "rejected" || status === "error"
              ? "bg-short"
              : status === "claude_skip"
                ? "bg-warn"
                : "bg-lo";

        return (
          <motion.span
            key={symbol}
            title={`${symbol}${status ? ` · ${status}` : ""}`}
            className={cn(
              "h-8 w-1.5 rounded-full opacity-70",
              tone,
              scanning && "origin-bottom",
            )}
            animate={
              scanning
                ? { scaleY: [0.35, 1, 0.5], opacity: [0.45, 0.9, 0.55] }
                : { scaleY: status ? 0.9 : 0.45, opacity: status ? 0.85 : 0.42 }
            }
            transition={
              scanning
                ? {
                    duration: 0.7 + (i % 4) * 0.12,
                    repeat: Infinity,
                    repeatType: "mirror",
                    ease: EASE_SNAP,
                    delay: i * 0.03,
                  }
                : { duration: 0.22, ease: EASE_SNAP }
            }
          />
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* card de semnal                                                      */
/* ------------------------------------------------------------------ */

/** --ease-snap din globals.css, in forma pe care o intelege framer-motion */
const EASE_SNAP: [number, number, number, number] = [0.23, 1, 0.32, 1];

function SignalCard({ result, index }: { result: SymbolResult; index: number }) {
  const reduceMotion = useReducedMotion();
  const trade = result.trade;
  const signal = result.signal;
  const approved = result.status === "approved" && trade && signal;

  // §9: intrarea semnalelor e rara, aici merge stagger-ul
  // approved cards get richer entrance (slide + fade + scale), rejected get lighter
  const enter = {
    initial: reduceMotion
      ? { opacity: 1, scale: 1 }
      : { opacity: 0, y: approved ? 8 : 6, scale: approved ? 0.98 : 1 },
    animate: { opacity: 1, y: 0, scale: 1 },
    transition: {
      duration: reduceMotion ? 0 : approved ? 0.28 : 0.22,
      delay: reduceMotion ? 0 : Math.min(index, 8) * 0.04,
      ease: EASE_SNAP,
    },
  };

  if (!approved) {
    return (
      <motion.div
        {...enter}
        className="cell flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3"
      >
        <span className="font-mono text-[13px] text-hi">{result.symbol}</span>
        <StatusBadge status={result.status} />
        {result.detail && (
          <p className="w-full text-[13px] text-mid sm:w-auto sm:flex-1">
            {result.detail}
          </p>
        )}
      </motion.div>
    );
  }

  const up = trade.side === "long";

  return (
    <motion.article {...enter} className="panel relative overflow-hidden">
      {/* muchia de directie. culoarea poarta sens de piata, deci e permisa */}
      <div className={cn("h-px w-full", up ? "bg-long" : "bg-short")} />

      <div className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            {up ? (
              <TrendingUp strokeWidth={1.5} className="h-4 w-4 text-long" />
            ) : (
              <TrendingDown strokeWidth={1.5} className="h-4 w-4 text-short" />
            )}
            <h3 className="font-mono text-[15px] text-hi">{result.symbol}</h3>
            <SidePill side={trade.side} />
          </div>
          <StatusBadge status="approved" />
        </div>

        <p className="num mt-2 font-mono text-[12px] tabular-nums text-lo">
          scor {fmt(signal.score, 0)}/100 · R:R {fmt(signal.risk_reward, 2)} ·
          stop {fmt(signal.stop_distance_pct * 100, 2)}%
        </p>

        <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
              <LiveMetric
            label="Intrare"
            value={fmtPrice(trade.entry)}
            helper={up ? "long" : "short"}
            tone={up ? "long" : "short"}
          />
          <Readout label="Stop" value={fmtPrice(trade.stop_loss)} tone="short" />
          {(trade.take_profits ?? []).map((tp, i) => (
            <Readout
              key={i}
              label={`TP${i + 1}`}
              value={fmtPrice(tp)}
              tone="long"
            />
          ))}
          <Readout label="Marime" value={fmt(trade.position_size, 4)} />
          <Readout label="Levier" value={`${fmt(trade.leverage, 1)}x`} />
          <Readout label="Risc" value={`${fmt(trade.risk_amount)} USDT`} />
          <Readout
            label="Lichidare"
            value={fmtPrice(trade.liquidation_price)}
            tone="mid"
          />
        </div>

        {!!signal.reasons?.length && (
          <p className="mt-4 text-[13px] leading-relaxed text-mid">
            {signal.reasons.join(" · ")}
          </p>
        )}

        {!!signal.warnings?.length && (
          <div className="mt-3">
            <Notice
              tone="warn"
              title="Atentie"
              text={signal.warnings.join(" · ")}
            />
          </div>
        )}

        {result.analysis && (
          <div className="mt-3 rounded-cell border border-line bg-raised p-3.5">
            <Label>
              Claude
              {result.analysis.verdict ? ` · ${result.analysis.verdict}` : ""}
              {result.analysis.confidence !== undefined
                ? ` · ${fmt(result.analysis.confidence, 0)}%`
                : ""}
            </Label>
            <p className="mt-2 text-[13px] leading-relaxed text-mid">
              {result.analysis.reasoning || "analiza disponibila"}
            </p>
            {result.analysis.invalidation && (
              <p className="mt-2 text-[13px] leading-relaxed text-lo">
                Invalidare: {result.analysis.invalidation}
              </p>
            )}
          </div>
        )}
      </div>
    </motion.article>
  );
}

/* ------------------------------------------------------------------ */
/* consola                                                             */
/* ------------------------------------------------------------------ */

export function AgentDashboard() {
  const [status, setStatus] = useState<Status | null>(null);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [backtests, setBacktests] = useState<BacktestReport[]>([]);
  const [connected, setConnected] = useState(false);
  const [startingScan, setStartingScan] = useState(false);

  /*
   * Aici era parallax pe header si un rAF care umplea o bara de progres.
   * Amandoua au picat: header-ul e `position: sticky`, iar un `transform` pe un
   * element sticky ii rupe pozitionarea in unele motoare; si oricum §9 spune
   * explicit "header, panouri, chrome - fara animatie". Bara de progres nu era
   * randata nicaieri, deci era un rAF care rula in gol.
   *
   * Bugetul de 3D si de cursor sta intreg pe Risk Envelope (§7), unde miscarea
   * chiar reprezinta date.
   */

  const loadScan = useCallback(async () => {
    try {
      const data = await api<ScanResult>("/api/last-scan");
      if (data.started_at) setScan(data);
    } catch {
      /* starea conexiunii vine din /api/status */
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const data = await api<{ signals: HistoryRecord[] }>("/api/history");
      setHistory(data.signals ?? []);
    } catch {
      /* ignora */
    }
  }, []);

  const loadBacktests = useCallback(async () => {
    try {
      const data = await api<{ reports: BacktestReport[] }>("/api/backtest");
      setBacktests(data.reports ?? []);
    } catch {
      /* ignora */
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const data = await api<Status>("/api/status");
      setStatus(data);
      setConnected(true);
      return data.scanning;
    } catch {
      setConnected(false);
      return false;
    }
  }, []);

  const [scanCompleteFlash, setScanCompleteFlash] = useState(false);

  /**
   * §10.2. Efectul vechi depindea de [status?.scanning], deci intervalul era
   * distrus si recreat la fiecare comutare, iar callback-ul inchidea peste un
   * `status` vechi. Aici starea de scanare traieste intr-un ref, poller-ul
   * porneste o singura data, iar ritmul se adapteaza: 3s cat scaneaza,
   * 12s in repaus. Taie apelurile in repaus de ~4x.
   */
  const scanningRef = useRef(false);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    let flashTimer: number | undefined;
    let first = true;

    async function tick() {
      if (!alive) return;

      // Prima trecere aduce tot ce e nevoie ca sa se deseneze consola. Sta aici,
      // nu in corpul efectului, ca sa nu porneasca un al doilea render sincron
      // imediat dupa montare (react-hooks/set-state-in-effect).
      if (first) {
        first = false;
        await Promise.all([loadScan(), loadHistory(), loadBacktests()]);
        if (!alive) return;
      }

      const wasScanning = scanningRef.current;
      const nowScanning = await loadStatus();
      scanningRef.current = nowScanning;

      // scanarea tocmai s-a incheiat: aprindem confirmarea, dar NU iesim din
      // tick. Varianta veche facea `return` aici si nu mai reprograma
      // niciodata timer-ul, deci consola ingheta dupa prima scanare terminata.
      if (wasScanning && !nowScanning) {
        setScanCompleteFlash(true);
        if (flashTimer !== undefined) window.clearTimeout(flashTimer);
        flashTimer = window.setTimeout(() => {
          if (alive) setScanCompleteFlash(false);
        }, 600);
      }

      // cat timp scaneaza, si inca o data imediat dupa ce s-a terminat
      if (nowScanning || wasScanning) {
        await loadScan();
        await loadHistory();
      }

      if (!alive) return;
      timer = window.setTimeout(tick, nowScanning ? 3000 : 12000);
    }

    void tick();

    return () => {
      alive = false;
      if (timer !== undefined) window.clearTimeout(timer);
      if (flashTimer !== undefined) window.clearTimeout(flashTimer);
    };
  }, [loadStatus, loadScan, loadHistory, loadBacktests]);

  async function startScan() {
    setStartingScan(true);
    try {
      await api<{ started: boolean }>("/api/scan", {
        method: "POST",
        body: "{}",
      });
      scanningRef.current = true;
      await loadStatus();
    } finally {
      setStartingScan(false);
    }
  }

  async function toggleAuto(enabled: boolean) {
    await api<{ auto_enabled: boolean }>("/api/auto", {
      method: "POST",
      body: JSON.stringify({ enabled, interval_min: 15 }),
    });
    await loadStatus();
  }

  const isScanning = Boolean(status?.scanning || startingScan);

  const approvedCount = useMemo(
    () => scan?.results?.filter((r) => r.status === "approved").length ?? 0,
    [scan],
  );

  const results = useMemo(
    () => [...(scan?.results ?? [])].sort((a, b) => order[a.status] - order[b.status]),
    [scan],
  );

  /*
   * Cele trei serii de mai jos alimenteaza blocul de instrumente. Toate sunt
   * derivate din raspunsurile reale ale API-ului Python. Daca API-ul nu a
   * intors nimic, componenta respectiva nu se randeaza - nu se completeaza cu
   * date sintetice.
   */

  /** ultimele semnale cu scor, in ordine cronologica (istoricul vine invers) */
  const scorePoints = useMemo<ScorePoint[]>(() => {
    return history
      .filter((h) => h.signal && Number.isFinite(h.signal.score))
      .slice(0, 40)
      .reverse()
      .map((h) => ({
        ts: h.ts,
        score: h.signal!.score,
        side: h.signal!.side,
        symbol: h.signal!.symbol,
        status: h.status,
      }));
  }, [history]);

  /** repartitia ultimei scanari pe deciziile agentului */
  const outcome = useMemo(() => {
    const rows = scan?.results ?? [];
    const count = (s: SymbolResult["status"]) =>
      rows.filter((r) => r.status === s).length;

    const slices: OutcomeSlice[] = [
      { label: "Aprobate", count: count("approved"), color: "var(--long)" },
      { label: "Respinse de risc", count: count("rejected"), color: "var(--short)" },
      { label: "Oprite de Claude", count: count("claude_skip"), color: "var(--warn)" },
      { label: "Fara setup", count: count("no_setup"), color: "var(--text-lo)" },
      { label: "Sarite", count: count("skipped"), color: "var(--line-lit)" },
      { label: "Erori", count: count("error"), color: "var(--short)" },
    ];

    return { slices, total: rows.length };
  }, [scan]);

  /** jurnal de executie: rezultatele ultimei scanari, cele decisive primele */
  const logLines = useMemo<LogLine[]>(() => {
    const stamp = scan?.finished_at ?? scan?.started_at ?? "";
    return [...(scan?.results ?? [])]
      .sort((a, b) => order[a.status] - order[b.status])
      .map((r) => ({
        ts: stamp,
        kind: r.status,
        symbol: r.symbol,
        text:
          r.detail ||
          (r.trade
            ? `intrare ${fmtPrice(r.trade.entry)} · stop ${fmtPrice(r.trade.stop_loss)} · ${fmt(r.trade.leverage, 1)}x`
            : "fara detalii"),
      }));
  }, [scan]);

  const ks = status?.kill_switch;
  const drawdown =
    ks && ks.peak_equity > 0
      ? Math.max(0, (ks.peak_equity - (status?.equity ?? ks.peak_equity)) / ks.peak_equity)
      : 0;
  // limita vine de la kill-switch, nu din UI. fallback pe valoarea din config.
  const maxDrawdown = ks?.max_drawdown ?? 0.15;

  return (
    <main className="min-h-dvh">
      {/* ============ rail de sus (§6.1) ============ */}
      {/* fundal opac, nu sticla: un rail e o piesa de carcasa, nu un geam (§12) */}
      <header className="sticky top-0 z-30 border-b border-line bg-void">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div>
            <h1 className="text-[15px] font-medium tracking-tight text-hi">
              Agent BingX
            </h1>
            <p
              className={cn(
                "num mt-0.5 font-mono text-[11px] tabular-nums",
                connected ? "text-lo" : "text-short",
              )}
            >
              {connected
                ? `api local online · ${isScanning ? "poll 3s" : "poll 12s"}`
                : "serverul python nu raspunde"}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <label className="flex min-h-11 cursor-pointer items-center gap-2 rounded-full border border-line px-3.5 text-[13px] text-mid transition-colors hover:border-line-lit">
              <input
                type="checkbox"
                className="h-4 w-4 accent-white"
                checked={Boolean(status?.auto_enabled)}
                onChange={(e) => toggleAuto(e.target.checked)}
              />
              auto 15m
            </label>

            <button
              onClick={startScan}
              disabled={isScanning || !connected}
              className={cn(
                "relative inline-flex min-h-11 items-center gap-2 overflow-hidden rounded-full px-5 text-[13px] font-medium",
                "bg-hi text-void shadow-elev-1",
                "transition-transform duration-150 ease-snap active:scale-[0.97]",
                "disabled:cursor-not-allowed disabled:opacity-45",
                isScanning && "sweep",
              )}
            >
              <Radio strokeWidth={1.5} className="h-4 w-4" />
              {isScanning ? "Scanez" : "Scaneaza acum"}
            </button>
          </div>
        </div>

        {/*
          Singura miscare permisa pe rail: linia care arata ca scanarea chiar
          ruleaza (§9). `transformOrigin: left` - fara el bara ar creste din
          centru in ambele directii, ceea ce nu inseamna "progres".
        */}
        {status?.scanning && (
          <motion.div
            className="pointer-events-none absolute bottom-0 left-0 h-px w-full"
            style={{
              background: "linear-gradient(90deg, var(--long), var(--warn))",
              transformOrigin: "left",
            }}
            animate={{ scaleX: [0, 1] }}
            transition={{ duration: 2.8, ease: "linear", repeat: Infinity }}
          />
        )}

        {/* scanarea s-a incheiat: o confirmare scurta, pe date, nu pe chrome */}
        {scanCompleteFlash && (
          <motion.div
            className="pointer-events-none absolute inset-0 bg-white/10"
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 0.12, 0] }}
            transition={{ duration: 0.4, ease: EASE_SNAP }}
          />
        )}
      </header>

      <div className="mx-auto max-w-[1400px] px-4 pb-16 sm:px-6">
        {/*
          Rail-ul de telemetrie si Risk Envelope au iesit din <header>: erau
          lipite intr-un bloc sticky care ocupa aproape jumatate de ecran pe
          laptop. Un rail sticky trebuie sa ramana subtire; instrumentele curg
          cu pagina.
        */}
        {status && (
          <div className="border-b border-line py-2.5">
            <TelemetryRail
              symbols={status.symbols}
              results={scan?.results ?? []}
              scanning={status.scanning}
            />
          </div>
        )}

        {/*
          ============ bloc de comanda ============

          Grila de tip bento, inspirata din exportul Stitch, dar cu ierarhia
          consolei: Risk Envelope (§7, singurul obiect 3D din pagina) tine
          coloana mare, iar repartitia scanarii sta langa el. Sub ele, curba de
          scoruri si jurnalul de executie - amandoua pe date reale.

          Celulele care nu au date nu se randeaza deloc. Un panou gol care
          spune "--" peste tot e mai rau decat absenta lui.
        */}
        <div className="mt-5 grid gap-4 lg:grid-cols-12">
          {ks && (
            <Panel className={cn("p-4 sm:p-5", outcome.total ? "lg:col-span-7" : "lg:col-span-12")}>
              <RiskEnvelope
                allowed={ks.allowed}
                statusLine={ks.status_line || ks.reason || "limite in parametri"}
                rings={[
                  {
                    label: "Trades azi",
                    value: ks.trades_today,
                    max: ks.max_trades,
                    readout: `${ks.trades_today}/${ks.max_trades}`,
                  },
                  {
                    label: "Pierderi cons.",
                    value: ks.consecutive_losses,
                    max: ks.max_consecutive,
                    readout: `${ks.consecutive_losses}/${ks.max_consecutive}`,
                  },
                  {
                    label: "Drawdown",
                    value: drawdown,
                    max: maxDrawdown,
                    readout: `${fmt(drawdown * 100, 1)}/${fmt(maxDrawdown * 100, 0)}%`,
                  },
                ]}
              />
            </Panel>
          )}

          {!!outcome.total && (
            <Panel className={cn("p-4 sm:p-5", ks ? "lg:col-span-5" : "lg:col-span-12")}>
              <Label>Repartitia ultimei scanari</Label>
              <div className="mt-4">
                <OutcomeDonut
                  slices={outcome.slices}
                  total={outcome.total}
                  caption={`${approvedCount} din ${outcome.total} au trecut de risk engine.`}
                />
              </div>
            </Panel>
          )}

          {scorePoints.length > 1 && (
            <Panel className="p-4 sm:p-5 lg:col-span-7">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <Label>Scorurile semnalelor din jurnal</Label>
                <span className="num font-mono text-[11px] tabular-nums text-lo">
                  cronologic · scor brut, nu profit
                </span>
              </div>
              <div className="mt-4">
                <ScoreChart points={scorePoints} />
              </div>
            </Panel>
          )}

          {!!logLines.length && (
            <Panel className={cn("p-4 sm:p-5", scorePoints.length > 1 ? "lg:col-span-5" : "lg:col-span-12")}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <Label>Jurnal de executie</Label>
                <span className="num font-mono text-[11px] tabular-nums text-lo">
                  {localTime(scan?.finished_at)}
                </span>
              </div>
              <div className="mt-3">
                <ExecLog lines={logLines} />
              </div>
            </Panel>
          )}
        </div>

        {/* ============ alerte ============ */}
        <div className="mt-5 space-y-2">
          {status?.last_error && (
            <Notice
              tone="short"
              title="Eroare la ultima scanare"
              text={status.last_error}
            />
          )}
          {status && !status.kill_switch.allowed && (
            <Notice
              tone="short"
              title="Kill-switch declansat"
              text={
                status.kill_switch.reason ||
                "Agentul nu mai genereaza semnale pana la reset."
              }
            />
          )}
          {status && !status.blackout.clear && (
            <Notice
              tone="warn"
              title="Fereastra de stiri"
              text={`${status.blackout.reasons.join("; ")}. Nu intra cu levier in conditii de spread marit.`}
            />
          )}
        </div>

        {/* ============ hartie cross-sectionala ============ */}
        {/*
          Strategie separata de scanner-ul BTC/ETH/SOL de mai jos - aceea are
          expectanta negativa masurata, asta a trecut validare walk-forward.
          Statiaza deasupra sectiunii vechi tocmai ca sa nu se confunde: e
          sistemul cu edge real, nu instrumentele de risc pentru cel fara.
        */}
        <PaperBookSection />

        {/* ============ semnale (§6.2) ============ */}
        <SectionHead
          title="Ultima scanare"
          meta={
            scan?.finished_at
              ? `${localTime(scan.finished_at)} · ${approvedCount} aprobate`
              : "nicio scanare rulata"
          }
        />
        <div className="space-y-2">
          {!scan?.started_at && (
            <Empty
              icon={Clock}
              text="Nicio scanare inca. Porneste una manual sau activeaza auto-scanarea."
            />
          )}
          {scan?.error && (
            <Notice tone="short" title="Scanare esuata" text={scan.error} />
          )}
          {scan?.started_at && !scan.error && !scan.kill_switch_ok && (
            <Empty
              icon={ShieldAlert}
              text={`Scanare oprita de kill-switch: ${scan.kill_switch_reason}`}
            />
          )}
          {scan?.started_at &&
            !scan.error &&
            scan.kill_switch_ok &&
            !scan.blackout_ok && (
              <Empty
                icon={AlertTriangle}
                text={`Scanare oprita de blackout: ${(scan.blackout_reasons ?? []).join("; ")}`}
              />
            )}
          {results.map((r, i) => (
            <SignalCard key={`${r.symbol}-${r.status}`} result={r} index={i} />
          ))}
        </div>

        {/* ============ instrumente de risc + stare sistem (§6.3) ============ */}
        <SectionHead title="Instrumente" />
        <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
          <Panel className="p-4 sm:p-5">
            <Label>Capital si risc</Label>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <LiveMetric
                label="Capital"
                value={status?.equity == null ? NIL : fmt(status.equity)}
                helper={
                  status?.equity == null
                    ? "fara chei api, calcul pe 1000 USDT"
                    : "sold real din BingX"
                }
                tone="hi"
              />
              <LiveMetric
                label="PnL azi"
                value={ks ? fmt(ks.pnl_today) : NIL}
                helper="USDT realizat"
                tone={ks && ks.pnl_today !== null && ks.pnl_today > 0 ? "long" : ks && ks.pnl_today !== null && ks.pnl_today < 0 ? "short" : "hi"}
              />
              <Readout
                label="Risc / trade"
                value={status ? `${fmt(status.risk.risk_per_trade * 100, 1)}%` : NIL}
              />
              <Readout
                label="Levier max"
                value={status ? `${fmt(status.risk.max_leverage, 0)}x` : NIL}
              />
              <Readout
                label="Timeframe"
                value={status ? `${status.risk.htf} / ${status.risk.ltf}` : NIL}
              />
              <Readout
                label="R:R minim"
                value={status ? `${fmt(status.risk.min_risk_reward, 1)}` : NIL}
              />
            </div>
          </Panel>

          <Panel className="p-4 sm:p-5">
            <Label>Stare sistem</Label>
            {status ? (
              <>
                <dl className="mt-3">
                  {(
                    [
                      [
                        "Cont BingX",
                        status.bingx_authenticated,
                        status.bingx_authenticated
                          ? "conectat"
                          : "doar date publice",
                      ],
                      [
                        "Analiza Claude",
                        status.claude_enabled,
                        status.claude_enabled ? "pornita" : "oprita",
                      ],
                      [
                        "Telegram",
                        status.telegram_enabled,
                        status.telegram_enabled ? "pornit" : "oprit",
                      ],
                      ["API local", connected, connected ? "online" : "offline"],
                    ] as const
                  ).map(([label, on, text]) => (
                    <div
                      key={label}
                      className="flex items-center justify-between border-b border-line py-2.5 last:border-0"
                    >
                      <dt className="text-[13px] text-mid">{label}</dt>
                      <dd
                        className={cn(
                          "font-mono text-[12px]",
                          on ? "text-hi" : "text-lo",
                        )}
                      >
                        {text}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="mt-3 border-t border-line pt-3">
                  <Label>Simboluri urmarite</Label>
                  <p className="mt-1.5 font-mono text-[12px] leading-relaxed text-mid">
                    {status.symbols.join("  ")}
                  </p>
                  {!!status.invalid_symbols.length && (
                    <p className="mt-2 font-mono text-[12px] text-warn">
                      ignorate: {status.invalid_symbols.join("  ")}
                    </p>
                  )}
                </div>
              </>
            ) : (
              <div className="mt-3">
                <Empty icon={Wifi} text="Astept conexiunea cu API-ul Python." />
              </div>
            )}
          </Panel>
        </div>

        {/* ============ istoric (§6.4) ============ */}
        <SectionHead
          title="Jurnal semnale"
          meta={`${history.length} inregistrari`}
        />
        <Panel className="overflow-hidden">
          <div className="long-list overflow-x-auto">
            <table className="w-full min-w-[620px] text-[13px]">
              <thead>
                <tr className="border-b border-line">
                  {["Cand", "Simbol", "Directie", "Intrare", "Stop", "Stare"].map(
                    (h) => (
                      <th key={h} className="px-4 py-3 text-left">
                        <Label>{h}</Label>
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {!history.length && (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center text-mid">
                      Niciun semnal in jurnal.
                    </td>
                  </tr>
                )}
                {history.map((h, i) => (
                  <tr
                    key={`${h.ts}-${i}`}
                    className="border-b border-line last:border-0"
                  >
                    <td className="num px-4 py-3 font-mono tabular-nums text-lo">
                      {localTime(h.ts)}
                    </td>
                    <td className="px-4 py-3 font-mono text-hi">
                      {h.signal?.symbol ?? NIL}
                    </td>
                    <td className="px-4 py-3">
                      {h.signal?.side ? <SidePill side={h.signal.side} /> : NIL}
                    </td>
                    <td className="num px-4 py-3 font-mono tabular-nums text-hi">
                      {fmtPrice(h.signal?.entry)}
                    </td>
                    <td className="num px-4 py-3 font-mono tabular-nums text-short">
                      {fmtPrice(h.signal?.stop_loss)}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={h.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        {/* ============ backtest (§6.5) ============ */}
        <SectionHead
          title="Backtest"
          meta="rezultatele testelor rulate, nu proiectii"
        />
        <Panel className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-[13px]">
              <thead>
                <tr className="border-b border-line">
                  {[
                    "Simbol",
                    "Trades",
                    "Win rate",
                    "Expectanta",
                    "Randament",
                    "Max DD",
                    "Perioada",
                  ].map((h) => (
                    <th key={h} className="px-4 py-3 text-left">
                      <Label>{h}</Label>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {!backtests.length && (
                  <tr>
                    <td colSpan={7} className="px-4 py-10 text-center text-mid">
                      Niciun backtest salvat.
                    </td>
                  </tr>
                )}
                {backtests.map((r) => (
                  <tr key={r.symbol} className="border-b border-line last:border-0">
                    <td className="px-4 py-3 font-mono text-hi">{r.symbol}</td>
                    <td className="num px-4 py-3 font-mono tabular-nums text-mid">
                      {r.total_trades}
                    </td>
                    <td className="num px-4 py-3 font-mono tabular-nums text-mid">
                      {fmt(r.win_rate * 100, 1)}%
                    </td>
                    <td
                      className={cn(
                        "num px-4 py-3 font-mono tabular-nums",
                        r.expectancy_r < 0 ? "text-short" : "text-long",
                      )}
                    >
                      {fmt(r.expectancy_r, 3)}R
                    </td>
                    <td
                      className={cn(
                        "num px-4 py-3 font-mono tabular-nums",
                        r.total_return_pct < 0 ? "text-short" : "text-long",
                      )}
                    >
                      {fmt(r.total_return_pct, 1)}%
                    </td>
                    <td className="num px-4 py-3 font-mono tabular-nums text-mid">
                      {fmt(r.max_drawdown_pct, 1)}%
                    </td>
                    <td className="num px-4 py-3 font-mono tabular-nums text-lo">
                      {r.period_start?.slice(0, 10)} → {r.period_end?.slice(0, 10)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* verdictul negativ ramane vizibil, nu ingropat (§6.5) */}
          <div className="border-t border-line bg-raised px-4 py-3.5">
            <p className="text-[13px] leading-relaxed text-warn">
              Verdict: nu tranzactiona strategia asta cu bani reali in forma
              actuala. Expectanta e negativa in testele rulate. Consola e pentru
              analiza si semnale manuale, nu pentru executie automata.
            </p>
          </div>
        </Panel>

        <footer className="mt-10 flex items-center gap-2 border-t border-line pt-4">
          <Activity strokeWidth={1.5} className="h-3.5 w-3.5 text-lo" />
          <p className="num font-mono text-[11px] tabular-nums text-lo">
            server {localTime(status?.server_time)}
          </p>
        </footer>
      </div>
    </main>
  );
}

export default AgentDashboard;
