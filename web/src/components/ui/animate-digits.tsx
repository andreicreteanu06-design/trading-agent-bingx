"use client";

/**
 * AnimateDigits - readout de instrument.
 *
 * Bazat pe "Animate Digits" de @unlumen (21st.dev), adaptat pentru consola:
 *  - import din "framer-motion" (pachetul "motion" nu e instalat aici)
 *  - parametrii de intrare taiati agresiv. Originalul foloseste blur 52px si
 *    deplasare 32px, ceea ce e corect pentru un timer pe care il vezi o data,
 *    dar e prea mult pentru un pret care se schimba la 3s (§9 din DESIGN.md).
 *  - key-ul celulelor e calculat de la dreapta, ca sa nu se re-animeze tot
 *    randul cand numarul creste in lungime (99.4 -> 100.2)
 *  - respecta prefers-reduced-motion: randeaza text simplu
 *
 * Animeaza doar cifrele care s-au schimbat efectiv. Asta e diferenta fata de
 * un ticker obisnuit, si e motivul pentru care merita componenta.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  AnimatePresence,
  motion,
  useSpring,
  useTransform,
} from "framer-motion";

import { cn } from "@/lib/utils";

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(cb: () => void) {
  const mq = window.matchMedia(REDUCED_QUERY);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}

function useReducedMotion() {
  return useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia(REDUCED_QUERY).matches,
    () => false,
  );
}

interface ExitItem {
  id: number;
  char: string;
  exitY: number;
}

let _id = 0;

/** cat de mult se misca o cifra la schimbare. deliberat mic. */
const ENTER_Y = 9;
const ENTER_BLUR = 5;
const ENTER_SCALE = 0.88;

function DigitCell({ char, isDigit }: { char: string; isDigit: boolean }) {
  const [exitQueue, setExitQueue] = useState<ExitItem[]>([]);
  const prevCharRef = useRef(char);
  const isFirstRender = useRef(true);

  const spring = { stiffness: 420, damping: 32 };
  const y = useSpring(0, spring);
  const opacity = useSpring(1, spring);
  const scale = useSpring(1, spring);
  const blur = useSpring(0, spring);
  const filter = useTransform(blur, (v) =>
    v < 0.05 ? "none" : `blur(${v}px)`,
  );

  useEffect(() => {
    if (!isDigit) return;

    const prev = prevCharRef.current;
    prevCharRef.current = char;

    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    if (char === prev || !/\d/.test(prev)) return;

    const up = Number(char) > Number(prev);
    const id = _id++;

    setExitQueue((q) => {
      const next = [...q, { id, char: prev, exitY: up ? -ENTER_Y : ENTER_Y }];
      return next.length > 2 ? next.slice(-2) : next;
    });

    y.jump(up ? ENTER_Y : -ENTER_Y);
    opacity.jump(0);
    scale.jump(ENTER_SCALE);
    blur.jump(ENTER_BLUR);

    y.set(0);
    opacity.set(1);
    scale.set(1);
    blur.set(0);
  }, [char, isDigit, y, opacity, scale, blur]);

  if (!isDigit) {
    return <span>{char}</span>;
  }

  return (
    <span className="relative grid place-items-center [&>*]:col-start-1 [&>*]:row-start-1">
      <AnimatePresence>
        {exitQueue.map(({ id, char: exitChar, exitY }) => (
          <motion.span
            key={id}
            aria-hidden
            initial={{ opacity: 1, scale: 1, filter: "blur(0px)", y: 0 }}
            animate={{
              opacity: 0,
              scale: ENTER_SCALE,
              filter: `blur(${ENTER_BLUR}px)`,
              y: exitY,
            }}
            transition={{ type: "spring", stiffness: 420, damping: 38 }}
            onAnimationComplete={() =>
              setExitQueue((q) => q.filter((item) => item.id !== id))
            }
          >
            {exitChar}
          </motion.span>
        ))}
      </AnimatePresence>
      <motion.span style={{ opacity, scale, filter, y }}>{char}</motion.span>
    </span>
  );
}

export function AnimateDigits({
  value,
  className,
}: {
  value: string;
  className?: string;
}) {
  const reduced = useReducedMotion();
  const chars = value.split("");

  if (reduced) {
    return (
      <span className={cn("num tabular-nums", className)}>{value}</span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center tabular-nums leading-none",
        className,
      )}
      // valoarea completa pentru cititoarele de ecran; celulele sunt vizuale
      aria-label={value}
      role="text"
    >
      {chars.map((char, i) => (
        <DigitCell
          // key de la dreapta: cand numarul creste in lungime, cifrele
          // existente isi pastreaza identitatea si nu se re-animeaza toate
          key={chars.length - 1 - i}
          char={char}
          isDigit={/\d/.test(char)}
        />
      ))}
    </span>
  );
}

export default AnimateDigits;
