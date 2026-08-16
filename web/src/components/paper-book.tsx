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
import { Layers, Wallet } from "lucide-react";

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

/** simbolul scurt, fara perechea de cotatie - mai usor de citit intr-un tabel dens */
function shortSymbol(sym: string) {
  return sym.split("/")[0] ?? sym;
}

export function PaperBookSection() {
  const [book, setBook] = useState<PaperBook | null>(null);
  const [connected, setConnected] = useState(false);
  const alive = useRef(true);

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
                ? `${book.positions.length} pozitii deschise`
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
                  {book.positions?.map((p) => (
                    <tr key={p.symbol} className="border-b border-line last:border-0">
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
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

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
