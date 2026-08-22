"use client";

/**
 * Grafic de lumanari, pentru detaliul unei singure monede din cartea de
 * hartie. Acelasi principiu ca ScoreChart: SVG crud, fara librarie externa,
 * o singura animatie la montare, restul e feedback direct la hover.
 *
 * Linia punctata orizontala arata pretul de referinta al pozitiei curente
 * (ultimul pret de marcaj din cartea de hartie) - acelasi limbaj vizual ca
 * pragul de 60 din ScoreChart.
 */

import { useId, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/utils";

export type Candle = {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

const VB_W = 640;
const VB_H = 220;
const PAD_Y = 12;

export function CandlestickChart({
  candles,
  referencePrice,
  referenceLabel,
  className,
}: {
  candles: Candle[];
  referencePrice?: number | null;
  referenceLabel?: string;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const [hot, setHot] = useState<number | null>(null);
  const gradientId = useId();

  const n = candles.length;
  if (n === 0) return null;

  let lo = Math.min(...candles.map((c) => c.low));
  let hi = Math.max(...candles.map((c) => c.high));
  if (referencePrice != null) {
    lo = Math.min(lo, referencePrice);
    hi = Math.max(hi, referencePrice);
  }
  const span = hi - lo || hi * 0.01 || 1; // evita impartirea la zero pe o piata perfect plata
  const yFor = (price: number) => VB_H - PAD_Y - ((price - lo) / span) * (VB_H - PAD_Y * 2);

  const slot = VB_W / n;
  const bodyW = Math.max(1, slot * 0.62);
  const xFor = (i: number) => slot * i + slot / 2;

  const active = hot === null ? null : candles[hot];

  return (
    <div className={cn("relative", className)}>
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        className="h-[220px] w-full"
        role="img"
        aria-label={`Lumanari recente, ${n} bare`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(255 255 255)" stopOpacity="0.05" />
            <stop offset="100%" stopColor="rgb(255 255 255)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {referencePrice != null && (
          <line
            x1="0"
            y1={yFor(referencePrice)}
            x2={VB_W}
            y2={yFor(referencePrice)}
            stroke="var(--line-lit)"
            strokeWidth={1}
            strokeDasharray="3 5"
            vectorEffect="non-scaling-stroke"
          />
        )}

        <motion.g
          initial={reduceMotion ? undefined : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.35, ease: [0.23, 1, 0.32, 1] }}
        >
          {candles.map((c, i) => {
            const up = c.close >= c.open;
            const color = up ? "var(--long)" : "var(--short)";
            const bodyTop = yFor(Math.max(c.open, c.close));
            const bodyBottom = yFor(Math.min(c.open, c.close));
            const bodyH = Math.max(1, bodyBottom - bodyTop);
            const cx = xFor(i);
            const on = hot === i;

            return (
              <g key={c.ts} opacity={hot === null || on ? 1 : 0.45}>
                <line
                  x1={cx}
                  x2={cx}
                  y1={yFor(c.high)}
                  y2={yFor(c.low)}
                  stroke={color}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
                <rect
                  x={cx - bodyW / 2}
                  y={bodyTop}
                  width={bodyW}
                  height={bodyH}
                  fill={color}
                />
              </g>
            );
          })}
        </motion.g>
      </svg>

      {/* fasii invizibile de hit-test, cate una per lumanare */}
      <div className="absolute inset-0 flex" onMouseLeave={() => setHot(null)}>
        {candles.map((c, i) => (
          <button
            key={`hit-${c.ts}`}
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            className="h-full flex-1 cursor-default"
            onMouseEnter={() => setHot(i)}
            onFocus={() => setHot(i)}
          />
        ))}
      </div>

      <div className="mt-2 flex h-4 items-center gap-2">
        {active ? (
          <p className="num font-mono text-[11px] tabular-nums text-mid">
            <span className={active.close >= active.open ? "text-long" : "text-short"}>
              {active.close.toFixed(active.close < 1 ? 6 : active.close < 100 ? 4 : 2)}
            </span>
            {" · O "}
            {active.open.toFixed(active.open < 1 ? 6 : active.open < 100 ? 4 : 2)}
            {" · H "}
            {active.high.toFixed(active.high < 1 ? 6 : active.high < 100 ? 4 : 2)}
            {" · L "}
            {active.low.toFixed(active.low < 1 ? 6 : active.low < 100 ? 4 : 2)}
            {" · "}
            {new Date(active.ts).toLocaleString("ro-RO", {
              day: "2-digit",
              month: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        ) : (
          <p className="num font-mono text-[11px] tabular-nums text-lo">
            {n} lumanari
            {referenceLabel ? ` · linia punctata = ${referenceLabel}` : ""}
          </p>
        )}
      </div>
    </div>
  );
}

export default CandlestickChart;
