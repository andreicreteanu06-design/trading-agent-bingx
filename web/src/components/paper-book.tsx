"use client";

/**
 * Cartea de hartie a strategiei cross-sectionale (DESIGN.md, aceleasi
 * primitive ca restul consolei - Panel/Readout/LiveMetric/SectionHead).
 *
 * O strategie complet separata de scanner-ul BTC/ETH/SOL de mai jos pe
 * pagina: aceea are expectanta negativa masurata, asta a trecut validare
 * walk-forward (vezi README, "Strategia cross-sectionala"). Sectiunea explica
 * asta explicit, ca sa nu se confunde cele doua sisteme.
 *
 * Sursa: execution/paper_executor.py, ruland fara ordine reale, la fiecare
 * cateva ore, pornit din RunAlways.ps1. Aceasta componenta doar citeste
 * /api/paper - nu trimite si nu poate trimite nimic catre bursa.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Layers, Wallet, X } from "lucide-react";
import { motion } from "framer-motion";

import {
  Empty,
  fmt,
  fmtPrice,
  Label,
  LiveMetric,
  localTime,
  NIL,
  Panel,
  Readout,
  SectionHead,
  api,
} from "@/components/agent-dashboard";
import { CandlestickChart, type Candle } from "@/components/ui/candlestick-chart";
import { cn } from "@/lib/utils";

type PaperPosition = {
  symbol: string;
  side: "long" | "short";
  notional_usdt: number;
  mark_price: number;
};

type PaperTrade = {
  symbol: string;
  delta_notional_usdt: number;
  fill_price: number;
  mid_price: number;
  slippage_bps: number;
  fee_usdt: number;
};

type PaperLastRun = {
  ts: string;
  gate_tradeable: boolean;
  gate_reason: string;
  trades: PaperTrade[];
};

type PaperBook = {
  exists: boolean;
  started_at?: string;
  last_updated_at?: string | null;
  capital_usdt?: number;
  equity_usdt?: number;
  price_pnl_usdt?: number;
  funding_paid_usdt?: number;
  fees_paid_usdt?: number;
  trade_count?: number;
  gross_exposure_usdt?: number;
  positions?: PaperPosition[];
  last_run?: PaperLastRun | null;
};

type SymbolDetail = {
  symbol: string;
  tf: string;
  candles: Candle[];
  range_pos: number | null;
  vol_24: number | null;
};

/** simbolul scurt, fara perechea de cotatie - mai usor de citit intr-un tabel dens */
function shortSymbol(sym: string) {
  return sym.split("/")[0] ?? sym;
}

/** unde sta moneda in intervalul propriu de 72 de lumanari - explica direct de ce e long/short */
function rangePosRead(v: number): { text: string; tone: "long" | "short" | "mid" } {
  if (v >= 0.7) return { text: "aproape de maximul ultimelor 72 de lumanari", tone: "long" };
  if (v <= 0.3) return { text: "aproape de minimul ultimelor 72 de lumanari", tone: "short" };
  return { text: "in mijlocul intervalului ultimelor 72 de lumanari", tone: "mid" };
}

export function PaperBookSection() {
  const [book, setBook] = useState<PaperBook | null>(null);
  const [connected, setConnected] = useState(false);
  const alive = useRef(true);

  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<SymbolDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const selectSymbol = useCallback(async (sym: string) => {
    // al doilea click pe acelasi rand inchide panoul, ca un disclosure normal
    if (selected === sym) {
      setSelected(null);
      setDetail(null);
      return;
    }
    setSelected(sym);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const data = await api<SymbolDetail>(
        `/api/paper/detail?symbol=${encodeURIComponent(sym)}`,
      );
      setDetail(data);
    } catch {
      setDetailError("Nu am putut aduce graficul - simbolul poate sa fi disparut din univers.");
    } finally {
      setDetailLoading(false);
    }
  }, [selected]);

  const load = useCallback(async () => {
    try {
      const data = await api<PaperBook>("/api/paper");
      if (alive.current) {
        setBook(data);
        setConnected(true);
      }
    } catch {
      if (alive.current) setConnected(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    // O carte rebalansata la ore, nu la secunde - pollingul rapid din restul
    // consolei (3-12s) n-ar arata nimic nou aici, doar ar bate API-ul degeaba.
    const timer = window.setInterval(load, 60_000);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [load]);

  const totalReturn =
    book?.exists && book.capital_usdt
      ? book.equity_usdt! / book.capital_usdt - 1
      : null;

  return (
    <>
      <SectionHead
        title="Hartie · cross-sectional"
        meta={
          book?.exists
            ? `actualizat ${localTime(book.last_updated_at ?? undefined)}`
            : connected
              ? "nicio rulare inca"
              : "api local nu raspunde"
        }
      />

      {!book?.exists ? (
        <Empty
          icon={Wallet}
          text={
            connected
              ? "Executorul de hartie nu a rulat inca. Porneste-l cu python execution\\paper_executor.py --capital <suma>."
              : "Astept conexiunea cu API-ul Python."
          }
        />
      ) : (
        <>
          <Panel className="overflow-hidden">
            <div className="p-4 sm:p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <Label>Echitate de hartie</Label>
                <span className="num font-mono text-[11px] tabular-nums text-lo">
                  {book.trade_count ?? 0} tranzactii de hartie de la pornire
                </span>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
                <LiveMetric
                  label="Echitate"
                  value={`${fmt(book.equity_usdt)} USDT`}
                  helper={`din ${fmt(book.capital_usdt)} USDT initial`}
                  tone={
                    totalReturn == null
                      ? "hi"
                      : totalReturn > 0
                        ? "long"
                        : totalReturn < 0
                          ? "short"
                          : "hi"
                  }
                />
                <Readout
                  label="Randament"
                  value={totalReturn == null ? NIL : `${fmt(totalReturn * 100, 2)}%`}
                  tone={
                    totalReturn == null
                      ? "hi"
                      : totalReturn > 0
                        ? "long"
                        : totalReturn < 0
                          ? "short"
                          : "hi"
                  }
                />
                <Readout
                  label="P&L de pret cumulat"
                  value={`${fmt(book.price_pnl_usdt)} USDT`}
                />
                <Readout
                  label="Funding cumulat"
                  value={`${fmt(book.funding_paid_usdt)} USDT`}
                />
                <Readout
                  label="Comisioane cumulate"
                  value={`${fmt(book.fees_paid_usdt)} USDT`}
                />
                <Readout
                  label="Expunere bruta"
                  value={`${fmt(book.gross_exposure_usdt)} USDT`}
                />
              </div>
            </div>

            {/* disclaimer permanent, nu doar la prima vizita - cifrele de mai
                sus se misca si tenteaza sa fie citite ca bani reali */}
            <div className="border-t border-line bg-raised px-4 py-3.5">
              <p className="text-[13px] leading-relaxed text-mid">
                Hartie, fara bani reali. Fill-uri la bid/ask real din carte,
                comisioane reale, funding real acumulat - dar niciun ordin nu
                pleaca vreodata spre bursa. Executorul ruleaza singur, la
                fiecare cateva ore.
              </p>
            </div>
          </Panel>

          <SectionHead
            title="Pozitii"
            meta={
              book.positions?.length
                ? `${book.positions.length} pozitii deschise · click pe un rand pentru grafic`
                : undefined
            }
          />
          <Panel className="overflow-hidden">
            <div className="long-list overflow-x-auto">
              <table className="w-full min-w-[520px] text-[13px]">
                <thead>
                  <tr className="border-b border-line">
                    {["Simbol", "Directie", "Notional", "Pret marcaj"].map((h) => (
                      <th key={h} className="px-4 py-3 text-left">
                        <Label>{h}</Label>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {!book.positions?.length && (
                    <tr>
                      <td colSpan={4} className="px-4 py-10 text-center text-mid">
                        Nicio pozitie deschisa.
                      </td>
                    </tr>
                  )}
                  {book.positions?.map((p) => {
                    const on = selected === p.symbol;
                    return (
                      <tr
                        key={p.symbol}
                        role="button"
                        tabIndex={0}
                        aria-pressed={on}
                        onClick={() => void selectSymbol(p.symbol)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            void selectSymbol(p.symbol);
                          }
                        }}
                        className={cn(
                          "cursor-pointer border-b border-line outline-none last:border-0",
                          "transition-colors hover:bg-raised focus-visible:bg-raised",
                          on && "bg-raised",
                        )}
                      >
                        <td className="px-4 py-3 font-mono text-hi">
                          {shortSymbol(p.symbol)}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={cn(
                              "rounded-full border px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em]",
                              p.side === "long"
                                ? "border-long/30 text-long"
                                : "border-short/30 text-short",
                            )}
                          >
                            {p.side}
                          </span>
                        </td>
                        <td className="num px-4 py-3 font-mono tabular-nums text-hi">
                          {fmt(p.notional_usdt)} USDT
                        </td>
                        <td className="num px-4 py-3 font-mono tabular-nums text-mid">
                          {fmtPrice(p.mark_price)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>

          {selected && (
            <motion.div
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
              className="mt-3"
            >
              <Panel className="overflow-hidden">
                <div className="flex items-center justify-between border-b border-line px-4 py-3 sm:px-5">
                  <Label>{shortSymbol(selected)} · grafic</Label>
                  <button
                    type="button"
                    onClick={() => {
                      setSelected(null);
                      setDetail(null);
                    }}
                    className="rounded-full p-1.5 text-lo transition-colors hover:bg-raised hover:text-hi"
                    aria-label="Inchide graficul"
                  >
                    <X strokeWidth={1.5} className="h-4 w-4" />
                  </button>
                </div>

                <div className="p-4 sm:p-5">
                  {detailLoading && (
                    <p className="py-8 text-center text-[13px] text-mid">
                      Aduc lumanarile...
                    </p>
                  )}
                  {detailError && (
                    <p className="py-8 text-center text-[13px] text-short">{detailError}</p>
                  )}
                  {detail && !detailLoading && !detailError && (
                    <>
                      <CandlestickChart
                        candles={detail.candles}
                        referencePrice={
                          book.positions?.find((p) => p.symbol === selected)?.mark_price
                        }
                        referenceLabel={`pret de referinta pozitie (${detail.tf})`}
                      />

                      <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
                        {(() => {
                          const pos = book.positions?.find((p) => p.symbol === selected);
                          const sameSide = (book.positions ?? []).filter(
                            (p) => p.side === pos?.side,
                          );
                          const rank = sameSide.findIndex((p) => p.symbol === selected) + 1;
                          const rp = detail.range_pos;
                          const read = rp == null ? null : rangePosRead(rp);
                          return (
                            <>
                              <Readout
                                label="Rang in carte"
                                value={
                                  pos && rank > 0
                                    ? `#${rank} din ${sameSide.length} ${pos.side}`
                                    : NIL
                                }
                              />
                              <Readout
                                label="range_pos"
                                value={rp == null ? NIL : `${fmt(rp * 100, 0)}%`}
                                tone={read?.tone ?? "hi"}
                              />
                              <Readout
                                label="Volatilitate (24 bare)"
                                value={
                                  detail.vol_24 == null
                                    ? NIL
                                    : `${fmt(detail.vol_24 * 100, 2)}%`
                                }
                              />
                              <Readout
                                label="Expunere in carte"
                                value={pos ? `${fmt(pos.notional_usdt)} USDT` : NIL}
                                tone={pos?.side === "long" ? "long" : "short"}
                              />
                            </>
                          );
                        })()}
                      </div>

                      {detail.range_pos != null && (
                        <p className="mt-3 text-[13px] leading-relaxed text-mid">
                          {shortSymbol(selected)} e {rangePosRead(detail.range_pos).text}.
                          Factorul range_pos merge long pe monedele aproape de maxim si
                          short pe cele aproape de minim - de aceea sta{" "}
                          {book.positions?.find((p) => p.symbol === selected)?.side ===
                          "long"
                            ? "long"
                            : "short"}{" "}
                          in cartea curenta.
                        </p>
                      )}
                    </>
                  )}
                </div>
              </Panel>
            </motion.div>
          )}

          {book.last_run && (
            <div className="mt-4 flex items-start gap-3 rounded-cell border border-line bg-raised p-3.5">
              <Layers strokeWidth={1.5} className="mt-0.5 h-4 w-4 shrink-0 text-lo" />
              <div className="min-w-0">
                <p className="text-[13px] font-medium text-hi">
                  Ultima rulare: {localTime(book.last_run.ts)} ·{" "}
                  <span
                    className={
                      book.last_run.gate_tradeable ? "text-long" : "text-warn"
                    }
                  >
                    {book.last_run.gate_tradeable ? "poarta deschisa" : "poarta inchisa"}
                  </span>
                </p>
                <p className="mt-1 text-[13px] leading-relaxed text-mid">
                  {book.last_run.gate_reason}
                  {book.last_run.trades.length > 0 &&
                    ` · ${book.last_run.trades.length} tranzactii de hartie in aceasta rulare`}
                </p>
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}

export default PaperBookSection;
