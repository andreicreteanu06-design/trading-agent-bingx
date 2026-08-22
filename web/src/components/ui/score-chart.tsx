"use client";

/**
 * Graficul de scoruri din jurnal.
 *
 * Deseneaza EXCLUSIV inregistrari reale din /api/history: fiecare punct e un
 * semnal care chiar a existat, cu scorul lui. Nu exista serie sintetica, nu
 * exista "equity curve" interpolata - API-ul Python nu expune un time-series de
 * capital, iar a desena unul ar insemna sa inventam performanta (exact defectul
 * scos din landing page).
 *
 * §4: linia si suprafata sunt acromatice. Scorul nu e profit, deci nu are voie
 * sa fie verde. Singurul lucru colorat e punctul, si e colorat dupa directia
 * semnalului (long/short) - acolo culoarea chiar poarta sens de piata.
 *
 * §9: o singura animatie, la montare, cand curba se deseneaza. Hover-ul e
 * feedback direct, nu animatie de decor.
 */

import { useId, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/utils";

export type ScorePoint = {
  ts: string;
  score: number;
  side: "long" | "short";
  symbol: string;
  status: string;
};

/* Sistemul de coordonate al desenului. preserveAspectRatio="none" intinde
   viewBox-ul pe latimea containerului; stroke-ul e protejat cu
   vector-effect, iar punctele sunt elemente HTML pozitionate procentual, ca
   sa nu devina elipse la intindere. */
const VB_W = 640;
const VB_H = 200;
const PAD_Y = 16;

function yFor(score: number) {
  const clamped = Math.min(100, Math.max(0, score));
  return VB_H - PAD_Y - (clamped / 100) * (VB_H - PAD_Y * 2);
}

export function ScoreChart({
  points,
  className,
}: {
  points: ScorePoint[];
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const [hot, setHot] = useState<number | null>(null);
  // id unic per instanta. Trei sparkline-uri cu acelasi id="gradient" au fost
  // deja un bug in pagina asta: browserul rezolva url(#id) la primul definit.
  const gradientId = useId();

  const n = points.length;
  if (n === 0) return null;

  const xFor = (i: number) => (n === 1 ? VB_W / 2 : (i / (n - 1)) * VB_W);

  const line = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yFor(p.score).toFixed(1)}`)
    .join(" ");
  const area = `${line} L${VB_W},${VB_H} L0,${VB_H} Z`;

  const active = hot === null ? null : points[hot];

  return (
    <div className={cn("relative", className)}>
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        className="h-[200px] w-full"
        role="img"
        aria-label={`Scorurile ultimelor ${n} semnale din jurnal`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(255 255 255)" stopOpacity="0.10" />
            <stop offset="100%" stopColor="rgb(255 255 255)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* prag de referinta: sub 60 semnalele sunt slabe. linie reala, nu grid decorativ */}
        <line
          x1="0"
          y1={yFor(60)}
          x2={VB_W}
          y2={yFor(60)}
          stroke="var(--line-lit)"
          strokeWidth={1}
          strokeDasharray="3 5"
          vectorEffect="non-scaling-stroke"
        />

        <motion.path
          d={area}
          fill={`url(#${gradientId})`}
          initial={reduceMotion ? undefined : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4, ease: [0.23, 1, 0.32, 1] }}
        />
        <motion.path
          d={line}
          fill="none"
          stroke="var(--text-mid)"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
          initial={reduceMotion ? undefined : { pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: reduceMotion ? 0 : 0.6, ease: [0.23, 1, 0.32, 1] }}
        />
      </svg>

      {/* Puncte + zone de hover, ca elemente HTML: raman rotunde indiferent
          cat de lat e containerul. */}
      <div className="pointer-events-none absolute inset-0">
        {points.map((p, i) => {
          const left = n === 1 ? 50 : (i / (n - 1)) * 100;
          const top = (yFor(p.score) / VB_H) * 100;
          const on = hot === i;
          return (
            <span
              key={`${p.ts}-${i}`}
              className={cn(
                "absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full transition-transform duration-150 ease-snap",
                p.side === "long" ? "bg-long" : "bg-short",
                on ? "scale-[1.9]" : "scale-100",
              )}
              style={{ left: `${left}%`, top: `${top}%`, opacity: on ? 1 : 0.72 }}
            />
          );
        })}
      </div>

      {/* fasii invizibile de hit-test, cate una per punct */}
      <div className="absolute inset-0 flex" onMouseLeave={() => setHot(null)}>
        {points.map((p, i) => (
          <button
            key={`hit-${p.ts}-${i}`}
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            className="h-full flex-1 cursor-default"
            onMouseEnter={() => setHot(i)}
            onFocus={() => setHot(i)}
          />
        ))}
      </div>

      {/* readout: apare doar la hover, langa marginea de sus */}
      <div className="mt-2 flex h-4 items-center gap-2">
        {active ? (
          <p className="num font-mono text-[11px] tabular-nums text-mid">
            <span className="text-hi">{active.symbol}</span>
            {" · scor "}
            <span className={active.side === "long" ? "text-long" : "text-short"}>
              {Math.round(active.score)}
            </span>
            {" · "}
            {active.side}
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
            {n} semnale in jurnal · linia punctata = pragul de 60
          </p>
        )}
      </div>
    </div>
  );
}

export default ScoreChart;
