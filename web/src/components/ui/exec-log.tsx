"use client";

/**
 * Jurnalul de executie, in forma de terminal.
 *
 * Forma vine din exportul Stitch ("live execution log"), continutul nu: fiecare
 * rand e derivat dintr-un eveniment care chiar s-a intamplat - un rezultat din
 * ultima scanare sau o inregistrare din /api/history. Nu exista linii
 * decorative, nu exista text generat ca sa umple ecranul.
 *
 * §9: randurile noi intra cu stagger de 40ms, ca semnalele. Nu exista cursor
 * care clipeste la infinit - ar fi un rAF permanent pe o pagina care sta
 * deschisa toata ziua, exact ce a fost scos din restul consolei.
 */

import { motion, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/utils";

export type LogLine = {
  ts: string;
  /** cheia de status din API, folosita si pentru ton */
  kind: "approved" | "rejected" | "claude_skip" | "error" | "no_setup" | "skipped" | "info";
  symbol: string;
  text: string;
};

const EASE_SNAP: [number, number, number, number] = [0.23, 1, 0.32, 1];

const TONE: Record<LogLine["kind"], { color: string; tag: string }> = {
  approved: { color: "text-long", tag: "OK  " },
  rejected: { color: "text-short", tag: "REJ " },
  error: { color: "text-short", tag: "ERR " },
  claude_skip: { color: "text-warn", tag: "SKIP" },
  no_setup: { color: "text-lo", tag: "----" },
  skipped: { color: "text-lo", tag: "----" },
  info: { color: "text-mid", tag: "INFO" },
};

function clock(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString("ro-RO", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function ExecLog({
  lines,
  className,
}: {
  lines: LogLine[];
  className?: string;
}) {
  const reduceMotion = useReducedMotion();

  return (
    <div
      className={cn(
        "long-list max-h-[300px] overflow-y-auto rounded-cell border border-line bg-void px-3 py-2.5",
        className,
      )}
      role="log"
      aria-live="polite"
      aria-label="jurnal de executie"
    >
      {!lines.length && (
        <p className="num py-6 text-center font-mono text-[12px] tabular-nums text-lo">
          fara evenimente. porneste o scanare.
        </p>
      )}

      {lines.map((l, i) => {
        const tone = TONE[l.kind] ?? TONE.info;
        return (
          <motion.p
            key={`${l.ts}-${l.symbol}-${i}`}
            initial={reduceMotion ? false : { opacity: 0, x: -4 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{
              duration: reduceMotion ? 0 : 0.2,
              delay: reduceMotion ? 0 : Math.min(i, 10) * 0.04,
              ease: EASE_SNAP,
            }}
            className="num flex gap-2.5 py-[3px] font-mono text-[11.5px] leading-relaxed tabular-nums"
          >
            <span className="shrink-0 text-lo">{clock(l.ts)}</span>
            <span className={cn("shrink-0 whitespace-pre", tone.color)}>{tone.tag}</span>
            <span className="shrink-0 text-hi">{l.symbol}</span>
            <span className="min-w-0 flex-1 truncate text-mid" title={l.text}>
              {l.text}
            </span>
          </motion.p>
        );
      })}
    </div>
  );
}

export default ExecLog;
