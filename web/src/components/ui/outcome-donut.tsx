"use client";

/**
 * Inelul de repartitie a scanarii.
 *
 * Adaptat dupa "Sectors Donut" (21st.dev, ssicevs) - structura de arce si
 * cross-highlight legenda<->arc sunt de acolo. Restul a fost rescris pentru
 * consola: paleta din DESIGN.md in loc de tokenii shadcn, si datele sunt
 * repartitia reala a ultimei scanari, nu alocare de portofoliu.
 *
 * §4: culorile saturate de aici poarta sensul deciziei agentului - aprobat
 * (long), respins/eroare (short), claude skip (warn). Restul, care nu inseamna
 * nimic pentru piata, ramane acromatic.
 */

import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/utils";

export type OutcomeSlice = {
  label: string;
  count: number;
  /** culoare CSS. acromatic pentru statusurile fara sens de piata */
  color: string;
};

const R = 52;
const STROKE = 13;
const C = 2 * Math.PI * R;
const EASE_SNAP: [number, number, number, number] = [0.23, 1, 0.32, 1];

export function OutcomeDonut({
  slices,
  total,
  caption,
  className,
}: {
  slices: OutcomeSlice[];
  total: number;
  caption: string;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const [hot, setHot] = useState<number | null>(null);

  const visible = slices.filter((s) => s.count > 0);
  if (!visible.length || total <= 0) return null;

  // reduce, nu `let acc` + reasignare: React 19 face render-ul strict pur,
  // asa ca reasignarea unei variabile din scope-ul componentei dupa map e
  // interzisa de react-hooks/immutability.
  const arcs = visible.reduce<Array<OutcomeSlice & { pct: number; start: number }>>(
    (out, s) => {
      const pct = (s.count / total) * 100;
      const start = out.length ? out[out.length - 1].start + out[out.length - 1].pct : 0;
      out.push({ ...s, pct, start });
      return out;
    },
    [],
  );

  return (
    <div className={cn("flex flex-wrap items-center gap-6", className)}>
      <div className="relative h-[132px] w-[132px] shrink-0">
        <svg width={132} height={132} viewBox="0 0 132 132" className="-rotate-90">
          {arcs.map((a, i) => (
            <motion.circle
              key={a.label}
              cx={66}
              cy={66}
              r={R}
              fill="none"
              stroke={a.color}
              strokeWidth={STROKE}
              strokeDasharray={`${Math.max(0, (a.pct / 100) * C - 2)} ${C}`}
              strokeDashoffset={-((a.start / 100) * C)}
              initial={reduceMotion ? false : { opacity: 0 }}
              animate={{ opacity: hot === null || hot === i ? 1 : 0.22 }}
              transition={
                reduceMotion
                  ? { duration: 0 }
                  : { duration: 0.32, ease: EASE_SNAP, delay: 0.06 * i }
              }
              onMouseEnter={() => setHot(i)}
              onMouseLeave={() => setHot(null)}
              style={{ cursor: "default" }}
            />
          ))}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="num font-mono text-[19px] tabular-nums text-hi">{total}</span>
          <span className="label mt-1">simboluri</span>
        </div>
      </div>

      <div className="flex min-w-[190px] flex-col">
        {arcs.map((a, i) => (
          <button
            key={a.label}
            type="button"
            onMouseEnter={() => setHot(i)}
            onMouseLeave={() => setHot(null)}
            onFocus={() => setHot(i)}
            onBlur={() => setHot(null)}
            className={cn(
              "-mx-2 flex min-h-8 items-center gap-2.5 rounded-cell px-2 py-1 text-left transition-opacity duration-200",
              hot !== null && hot !== i && "opacity-40",
            )}
          >
            <span
              className="h-2 w-2 shrink-0 rounded-[3px]"
              style={{ background: a.color }}
            />
            <span className="flex-1 truncate text-[12px] text-mid">{a.label}</span>
            <span className="num font-mono text-[11px] tabular-nums text-lo">
              {a.count}
            </span>
          </button>
        ))}
        <p className="mt-2 border-t border-line pt-2 text-[11px] leading-relaxed text-lo">
          {caption}
        </p>
      </div>
    </div>
  );
}

export default OutcomeDonut;
