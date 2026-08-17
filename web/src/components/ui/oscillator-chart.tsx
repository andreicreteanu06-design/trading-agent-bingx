"use client";

/**
 * Oscilatoare sub graficul de lumanari: RSI, histograma MACD, Stochastic RSI.
 *
 * CONTEXT, NU MOTIV. Strategia cross-sectionala nu citeste niciodata acesti
 * oscilatori - cartea se construieste exclusiv din rangul lui range_pos. Sunt
 * aici pentru ca sunt utili cand te uiti la un grafic, si atat. Sectiunea care
 * ii afiseaza trebuie sa spuna asta in text, nu doar in acest comentariu:
 * analiza tehnica clasica a fost masurata pe acest proiect, pe 4111 semnale,
 * cu o corelatie scor-rezultat de +0.026.
 *
 * Aceleasi principii ca CandlestickChart si ScoreChart: SVG crud, fara
 * librarie, o singura animatie la montare.
 */

import { useId } from "react";
import { motion, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/utils";

const VB_W = 640;
const VB_H = 64;
const PAD_Y = 6;

type Band = { from: number; to: number };

/** curata seria de NaN-uri de la inceput (perioada de incalzire a indicatorului) */
function firstValid(values: (number | null)[]) {
  return values.findIndex((v) => v != null);
}

function pathFor(
  values: (number | null)[],
  lo: number,
  hi: number,
  start: number,
) {
  const n = values.length;
  const span = hi - lo || 1;
  const slot = VB_W / n;
  const y = (v: number) => VB_H - PAD_Y - ((v - lo) / span) * (VB_H - PAD_Y * 2);

  let d = "";
  let open = false;
  for (let i = start; i < n; i++) {
    const v = values[i];
    if (v == null) {
      open = false;
      continue;
    }
    const x = slot * i + slot / 2;
    d += `${open ? "L" : "M"}${x.toFixed(2)} ${y(v).toFixed(2)} `;
    open = true;
  }
  return d.trim();
}

/**
 * Un singur panou de oscilator. `bands` marcheaza zonele conventionale
 * (supracumparat/supravandut) ca referinta vizuala, nu ca semnal.
 */
export function OscillatorChart({
  label,
  values,
  second,
  lo,
  hi,
  bands,
  histogram,
  readout,
  className,
}: {
  label: string;
  values: (number | null)[];
  second?: (number | null)[];
  lo: number;
  hi: number;
  bands?: Band[];
  histogram?: boolean;
  readout?: string;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const clipId = useId();

  const n = values.length;
  if (n === 0) return null;
  const start = firstValid(values);
  if (start < 0) return null;

  const span = hi - lo || 1;
  const slot = VB_W / n;
  const yFor = (v: number) =>
    VB_H - PAD_Y - ((v - lo) / span) * (VB_H - PAD_Y * 2);

  return (
    <div className={cn("min-w-0", className)}>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-lo">
          {label}
        </span>
        {readout && (
          <span className="num font-mono text-[11px] tabular-nums text-mid">
            {readout}
          </span>
        )}
      </div>
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        className="h-16 w-full"
        role="img"
        aria-label={`${label}${readout ? `, valoare curenta ${readout}` : ""}`}
      >
        <clipPath id={clipId}>
          <rect x="0" y="0" width={VB_W} height={VB_H} />
        </clipPath>

        {bands?.map((b, i) => (
          <rect
            key={i}
            x="0"
            y={yFor(b.to)}
            width={VB_W}
            height={Math.abs(yFor(b.from) - yFor(b.to))}
            className="fill-line/40"
          />
        ))}

        {/* linia de mijloc: zero pentru MACD, 50 pentru oscilatorii marginiti */}
        <line
          x1="0"
          x2={VB_W}
          y1={yFor(lo < 0 ? 0 : (lo + hi) / 2)}
          y2={yFor(lo < 0 ? 0 : (lo + hi) / 2)}
          className="stroke-line"
          strokeWidth="1"
          strokeDasharray="3 4"
          vectorEffect="non-scaling-stroke"
        />

        <g clipPath={`url(#${clipId})`}>
          {histogram ? (
            values.map((v, i) => {
              if (v == null) return null;
              const zero = yFor(0);
              const y = yFor(v);
              return (
                <rect
                  key={i}
                  x={slot * i + slot * 0.2}
                  y={Math.min(zero, y)}
                  width={Math.max(0.8, slot * 0.6)}
                  height={Math.max(0.8, Math.abs(zero - y))}
                  className={v >= 0 ? "fill-long/70" : "fill-short/70"}
                />
              );
            })
          ) : (
            <>
              {second && (
                <path
                  d={pathFor(second, lo, hi, firstValid(second))}
                  fill="none"
                  className="stroke-lo"
                  strokeWidth="1"
                  strokeDasharray="3 3"
                  vectorEffect="non-scaling-stroke"
                />
              )}
              <motion.path
                d={pathFor(values, lo, hi, start)}
                fill="none"
                className="stroke-hi"
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
                initial={reduceMotion ? false : { pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 0.45, ease: [0.23, 1, 0.32, 1] }}
              />
            </>
          )}
        </g>
      </svg>
    </div>
  );
}
