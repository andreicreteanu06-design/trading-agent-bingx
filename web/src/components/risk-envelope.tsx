"use client";

/**
 * Risk Envelope (DESIGN.md §7).
 *
 * Piesa de semnatura a consolei. Randeaza starea kill-switch-ului ca obiect
 * fizic: trei inele suprapuse in adancime, fiecare cu o limita de risc reala.
 *
 *  - inel exterior: trade-uri azi / maxim
 *  - inel mijlociu: pierderi consecutive / maxim
 *  - inel interior : drawdown fata de peak equity
 *
 * Se inclina cateva grade la miscarea cursorului, prin useMotionValue si
 * useSpring. Niciodata useState pentru pozitia cursorului: ar re-randa
 * arborele la fiecare pixel.
 *
 * Fiecare pixel din el e data. transform-style: preserve-3d, ~2KB, zero WebGL.
 */

import { useRef } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";

type Ring = {
  label: string;
  value: number;
  max: number;
  /** text scurt sub eticheta, citit de operator */
  readout: string;
};

/** neutrul inelului: 3.5:1 pe santul #1a1e24, pragul pentru glife UI (§11) */
const RING_NEUTRAL = "#6b7280";

/** acromatic pana cand conteaza. culoarea apare doar cand riscul urca. */
function ringColor(ratio: number) {
  if (ratio >= 1) return "var(--short)";
  if (ratio >= 0.66) return "var(--warn)";
  return RING_NEUTRAL;
}

function RingLayer({
  ratio,
  size,
  z,
  thickness,
}: {
  ratio: number;
  size: number;
  z: number;
  thickness: number;
}) {
  const pct = Math.max(0, Math.min(1, ratio));
  const deg = pct * 360;
  const color = ringColor(pct);

  return (
    <div
      className="absolute top-1/2 left-1/2"
      style={{
        width: size,
        height: size,
        marginLeft: -size / 2,
        marginTop: -size / 2,
        transform: `translateZ(${z}px)`,
        transformStyle: "preserve-3d",
      }}
    >
      {/* santul: inelul gol, adancit in material */}
      <div
        className="absolute inset-0 rounded-full"
        style={{
          background: "#1a1e24",
          mask: `radial-gradient(farthest-side, transparent calc(100% - ${thickness}px), #000 calc(100% - ${thickness}px))`,
          WebkitMask: `radial-gradient(farthest-side, transparent calc(100% - ${thickness}px), #000 calc(100% - ${thickness}px))`,
          boxShadow: "inset 0 1px 0 rgb(255 255 255 / .05)",
        }}
      />
      {/* umplerea: cat din limita e consumat */}
      <div
        className="absolute inset-0 rounded-full transition-[background] duration-300"
        style={{
          background: `conic-gradient(from -90deg, ${color} ${deg}deg, transparent ${deg}deg)`,
          mask: `radial-gradient(farthest-side, transparent calc(100% - ${thickness}px), #000 calc(100% - ${thickness}px))`,
          WebkitMask: `radial-gradient(farthest-side, transparent calc(100% - ${thickness}px), #000 calc(100% - ${thickness}px))`,
        }}
      />
    </div>
  );
}

export function RiskEnvelope({
  rings,
  allowed,
  statusLine,
}: {
  rings: [Ring, Ring, Ring];
  allowed: boolean;
  statusLine: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);

  const px = useMotionValue(0);
  const py = useMotionValue(0);

  const spring = { stiffness: 150, damping: 18, mass: 0.6 };
  const rotateY = useSpring(useTransform(px, [-0.5, 0.5], [-13, 13]), spring);
  const rotateX = useSpring(useTransform(py, [-0.5, 0.5], [10, -10]), spring);

  function onMove(e: React.PointerEvent) {
    const el = hostRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width - 0.5);
    py.set((e.clientY - r.top) / r.height - 0.5);
  }

  function onLeave() {
    px.set(0);
    py.set(0);
  }

  const worst = Math.max(...rings.map((r) => (r.max > 0 ? r.value / r.max : 0)));

  return (
    <div
      ref={hostRef}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      className="flex items-center gap-5"
      style={{ perspective: 620 }}
    >
      <motion.div
        className="relative shrink-0"
        style={{
          width: 108,
          height: 108,
          rotateX,
          rotateY,
          transformStyle: "preserve-3d",
        }}
      >
        {/* placa de baza: fundul carcasei */}
        <div
          className="absolute inset-0 rounded-full"
          style={{
            transform: "translateZ(-14px)",
            background: "#0b0d10",
            boxShadow: "0 10px 26px -10px rgb(0 0 0 / .85)",
          }}
        />

        <RingLayer ratio={rings[0].value / (rings[0].max || 1)} size={104} z={0} thickness={5} />
        <RingLayer ratio={rings[1].value / (rings[1].max || 1)} size={78} z={11} thickness={5} />
        <RingLayer ratio={rings[2].value / (rings[2].max || 1)} size={52} z={22} thickness={5} />

        {/* miezul: verdictul */}
        <div
          className="absolute top-1/2 left-1/2 grid place-items-center"
          style={{
            width: 40,
            height: 40,
            marginLeft: -20,
            marginTop: -20,
            transform: "translateZ(30px)",
          }}
        >
          <span
            className="font-mono text-[11px] font-medium tracking-tight"
            style={{ color: allowed ? "var(--text-hi)" : "var(--short)" }}
          >
            {allowed ? "OK" : "STOP"}
          </span>
        </div>

        {/* sticla: specular din aceeasi sursa de lumina ca panourile (§12) */}
        <div
          className="pointer-events-none absolute inset-0 rounded-full"
          style={{
            transform: "translateZ(34px)",
            background:
              "linear-gradient(160deg, rgb(255 255 255 / .07) 0%, transparent 42%)",
          }}
        />
      </motion.div>

      <div className="min-w-0 flex-1">
        <div className="label">Risk envelope</div>
        <p
          className="mt-1.5 truncate text-[13px]"
          style={{ color: allowed ? "var(--text-mid)" : "var(--short)" }}
        >
          {statusLine}
        </p>
        <dl className="mt-3 grid grid-cols-3 gap-x-3 gap-y-1">
          {rings.map((r) => {
            const ratio = r.max > 0 ? r.value / r.max : 0;
            const c = ringColor(ratio);
            return (
              <div key={r.label} className="min-w-0">
                <dt className="label truncate">{r.label}</dt>
                <dd
                  className="num mt-1 font-mono text-[13px] tabular-nums"
                  // sub prag inelul e acromatic, dar cifra ramane lizibila
                  style={{ color: c === RING_NEUTRAL ? "var(--text-hi)" : c }}
                >
                  {r.readout}
                </dd>
              </div>
            );
          })}
        </dl>
      </div>

      {/* valoarea agregata, pentru cititoare de ecran */}
      <span className="sr-only">
        Consum maxim de limita: {Math.round(worst * 100)} la suta.
      </span>
    </div>
  );
}

export default RiskEnvelope;
