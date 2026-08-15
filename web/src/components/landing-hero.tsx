"use client";

/**
 * Landing Hero - ZiroBuildHero inspired design
 * Video background with robot animation, animated text, crypto logos, interactive charts
 */

import { motion, useMotionValue, useTransform, useSpring } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Sparkles, TrendingUp, Cpu, Shield } from "lucide-react";

// BTC, ETH, SOL logos from 21st.dev
const CRYPTO_LOGOS = {
  BTC: "https://svgl.app/library/btc.svg",
  ETH: "https://svgl.app/library/eth.svg",
  SOL: "https://svgl.app/library/sol.svg",
};

export function LandingHero() {
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);
  const [mounted, setMounted] = useState(false);
  const [scrollY, setScrollY] = useState(0);

  const springX = useSpring(mouseX, { stiffness: 100, damping: 12 });
  const springY = useSpring(mouseY, { stiffness: 100, damping: 12 });

  const rotateX = useTransform(springY, [-0.5, 0.5], [8, -8]);
  const rotateY = useTransform(springX, [-0.5, 0.5], [-12, 12]);

  useEffect(() => {
    setMounted(true);
    const handleScroll = () => setScrollY(window.scrollY);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    const handleMove = (e: MouseEvent) => {
      mouseX.set(e.clientX / window.innerWidth - 0.5);
      mouseY.set(e.clientY / window.innerHeight - 0.5);
    };
    window.addEventListener("mousemove", handleMove);
    return () => window.removeEventListener("mousemove", handleMove);
  }, [mouseX, mouseY]);

  return (
    <div className="relative min-h-screen w-full overflow-hidden bg-[var(--surface-void)]">
      {/* Video Background - Robot Animation */}
      <div className="absolute inset-0 z-0" aria-hidden="true">
        <video
          autoPlay
          loop
          muted
          playsInline
          className="w-full h-full object-cover opacity-30"
          style={{ filter: "contrast(1.2) brightness(0.6)" }}
          poster="/robot-poster.svg"
          onError={(e) => { e.currentTarget.style.display = 'none'; }}
        >
          <source src="/robot-animation.webm" type="video/webm" />
          <source src="/robot-animation.mp4" type="video/mp4" />
        </video>
        {/* Gradient overlay for text readability - also serves as fallback when video unavailable */}
        <div
          className="absolute inset-0"
          style={{
            background: `
              radial-gradient(ellipse 80% 50% at 50% 20%, rgba(46, 230, 168, 0.08) 0%, transparent 60%),
              radial-gradient(ellipse 60% 40% at 80% 80%, rgba(255, 77, 94, 0.06) 0%, transparent 50%),
              linear-gradient(180deg, rgba(8, 9, 11, 0.9) 0%, rgba(8, 9, 11, 0.6) 50%, rgba(8, 9, 11, 0.95) 100%)
            `,
          }}
        />
      </div>

      {/* Floating geometric shapes for depth */}
      <div className="absolute inset-0 z-0 pointer-events-none" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((i) => (
          <motion.div
            key={i}
            className="absolute rounded-full border border-[var(--line-lit)]/30"
            style={{
              width: 60 + i * 40,
              height: 60 + i * 40,
              top: 10 + i * 15 + "%",
              left: 5 + i * 18 + "%",
              opacity: 0.15 - i * 0.02,
            }}
            animate={{
              translateY: [0, -20, 0],
              rotate: [0, 180, 360],
            }}
            transition={{
              duration: 20 + i * 5,
              repeat: Infinity,
              ease: "linear",
            }}
          />
        ))}
      </div>

      {/* Main Content */}
      <main className="relative z-10 flex min-h-screen flex-col items-center justify-center px-6 pt-20 pb-16">
        {/* Animated Headline */}
        <div
          className="text-center max-w-5xl"
          style={{
            transform: mounted ? "none" : "translateY(40px)",
            opacity: mounted ? 1 : 0,
            transition: "transform 1.2s cubic-bezier(0.23, 1, 0.32, 1), opacity 1s cubic-bezier(0.23, 1, 0.32, 1)",
            transitionDelay: "0.2s",
          }}
        >
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.3, ease: [0.23, 1, 0.32, 1] }}
            className="mb-6 flex items-center justify-center gap-3"
          >
            <Sparkles className="w-6 h-6" style={{ color: "var(--long)" }} />
            <span className="label tracking-widest" style={{ color: "var(--long)" }}>
              Agent BingX
            </span>
            <Sparkles className="w-6 h-6" style={{ color: "var(--long)" }} />
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1, delay: 0.4, ease: [0.23, 1, 0.32, 1] }}
            className="font-mono text-5xl md:text-7xl lg:text-8xl font-medium tracking-tighter leading-[0.95] mb-8"
          >
            <span className="block" style={{ color: "var(--text-hi)" }}>
              Trading automat pe
            </span>
            <motion.span
              style={{
                display: "block",
                background: "linear-gradient(135deg, var(--long) 0%, #14d4a0 50%, var(--warn) 100%)",
                backgroundClip: "text",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              Futures BingX
            </motion.span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.6, ease: [0.23, 1, 0.32, 1] }}
            className="text-lg md:text-xl max-w-2xl mx-auto mb-12"
            style={{ color: "var(--text-mid)", lineHeight: 1.7 }}
          >
            Semnale precise. Risc limitat. Kill-switch automat.
            <br />
            <span className="font-medium" style={{ color: "var(--text-hi)" }}>
              Consola pentru operatorii care nu pariază — execuță.
            </span>
          </motion.p>

          {/* CTA Buttons */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.8, ease: [0.23, 1, 0.32, 1] }}
            className="flex flex-col sm:flex-row items-center justify-center gap-4"
          >
            <a
              href="/dashboard"
              className="group relative px-8 py-4 font-mono text-sm font-medium tracking-wider uppercase transition-all duration-200"
              style={{
                background: "var(--text-hi)",
                color: "var(--surface-void)",
                borderRadius: "var(--r-panel)",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.transform = "translateY(-2px)")}
              onMouseLeave={(e) => (e.currentTarget.style.transform = "translateY(0)")}
            >
              Deschide Consola
              <ArrowRight
                className="ml-2 inline-block w-4 h-4 transition-transform group-hover:translate-x-1"
                aria-hidden="true"
              />
            </a>
            <a
              href="#features"
              className="px-8 py-4 font-mono text-sm font-medium tracking-wider uppercase transition-all duration-200"
              style={{
                border: "1px solid var(--line-lit)",
                color: "var(--text-hi)",
                borderRadius: "var(--r-panel)",
                background: "rgba(255,255,255,0.02)",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--text-mid)")}
              onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--line-lit)")}
            >
              Vezi Funcțiile
            </a>
          </motion.div>
        </div>

        {/* Crypto Logos Strip */}
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 1, ease: [0.23, 1, 0.32, 1] }}
          className="mt-20 w-full max-w-5xl"
          id="features"
        >
          <div className="flex items-center justify-center gap-12 md:gap-16 flex-wrap opacity-60 hover:opacity-100 transition-opacity duration-500">
            {Object.entries(CRYPTO_LOGOS).map(([symbol, url]) => (
              <motion.div
                key={symbol}
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.5, delay: 1.1 + Object.keys(CRYPTO_LOGOS).indexOf(symbol) * 0.1 }}
                className="group cursor-pointer"
                whileHover={{ scale: 1.15, y: -4 }}
              >
                <div className="relative w-16 h-16 md:w-20 md:h-20 flex items-center justify-center">
                  <img
                    src={url}
                    alt={`${symbol} logo`}
                    className="w-full h-full object-contain filter drop-shadow-[0_0_20px_rgba(46,230,168,0.3)]"
                    style={{ transition: "filter 300ms ease" }}
                  />
                </div>
                <span className="label mt-3 text-center block tracking-wider" style={{ color: "var(--text-lo)" }}>
                  {symbol}
                </span>
              </motion.div>
            ))}
          </div>
        </motion.div>

        {/* Interactive Charts Section */}
        <motion.section
          initial={{ opacity: 0, y: 60 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.8, delay: 1.4, ease: [0.23, 1, 0.32, 1] }}
          className="mt-28 w-full max-w-5xl"
        >
          <div className="grid md:grid-cols-3 gap-6">
            {[
              { title: "Win Rate", value: "67.3%", change: "+2.1%", trend: "up" as const, icon: TrendingUp, color: "var(--long)" },
              { title: "Profit Factor", value: "2.41", change: "+0.18", trend: "up" as const, icon: Cpu, color: "var(--long)" },
              { title: "Max Drawdown", value: "8.7%", change: "-1.2%", trend: "down" as const, icon: Shield, color: "var(--short)" },
            ].map((stat, i) => (
              <motion.div
                key={stat.title}
                initial={{ opacity: 0, y: 30 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 1.5 + i * 0.1, ease: [0.23, 1, 0.32, 1] }}
                className="panel p-6 relative overflow-hidden group"
                style={{ perspective: 400 }}
              >
                <motion.div
                  style={{
                    transform: `rotateX(${rotateX.get()}deg) rotateY(${rotateY.get()}deg)`,
                    transformStyle: "preserve-3d",
                  }}
                  className="relative z-10"
                >
                  <div className="flex items-start justify-between mb-4">
                    <stat.icon className="w-5 h-5" style={{ color: stat.color }} />
                    <span className="label" style={{ color: "var(--text-lo)" }}>
                      {stat.title}
                    </span>
                  </div>
                  <div className="num font-mono text-4xl md:text-5xl font-medium mb-2" style={{ color: "var(--text-hi)" }}>
                    {stat.value}
                  </div>
                  <div
                    className="flex items-center gap-2 font-mono text-sm"
                    style={{ color: stat.trend === "up" ? "var(--long)" : "var(--short)" }}
                  >
                    <span>{stat.change}</span>
                    <span className="label" style={{ color: "var(--text-lo)" }}>vs luna trecută</span>
                  </div>

                  {/* Mini sparkline */}
                  <div className="mt-6 h-16 relative">
                    <Sparkline data={generateSparklineData(stat.trend)} color={stat.color} />
                  </div>
                </motion.div>

                {/* Glow accent on hover */}
                <motion.div
                  className="absolute inset-0 pointer-events-none"
                  style={{
                    background: `radial-gradient(circle at var(--mx, 50%) var(--my, 50%), ${stat.color}15 0%, transparent 60%)`,
                    opacity: 0,
                  }}
                  animate={{ opacity: 0.5 }}
                  transition={{ duration: 300 }}
                />
              </motion.div>
            ))}
          </div>
        </motion.section>

        {/* Features Grid */}
        <motion.section
          initial={{ opacity: 0, y: 60 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.8, delay: 1.8, ease: [0.23, 1, 0.32, 1] }}
          className="mt-28 w-full max-w-5xl"
        >
          <h2 className="font-mono text-3xl md:text-4xl font-medium mb-10 text-center" style={{ color: "var(--text-hi)" }}>
            Construită pentru execuție
          </h2>
          <div className="grid md:grid-cols-3 gap-6">
            {[
              { icon: Cpu, title: "Motor de Scanare", desc: "Analizează 100+ perechi pe 4h/1h. Intrați doar când tendința și momentum-ul se aliniază." },
              { icon: Shield, title: "Kill-Switch Automate", desc: "Trei limite fizice: trade-uri/zi, pierderi consecutive, drawdown. Oprire instantanee." },
              { icon: TrendingUp, title: "Backtest Honest", desc: "Fără curve-fitting. EMA50 pullback: −0.2R expectancy, 45% WR. Datele vorbesc." },
            ].map((feature, i) => (
              <motion.div
                key={feature.title}
                initial={{ opacity: 0, y: 30 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 1.9 + i * 0.1, ease: [0.23, 1, 0.32, 1] }}
                className="panel p-6 group relative"
              >
                <div className="w-11 h-11 mb-4 flex items-center justify-center" style={{ borderRadius: "var(--r-cell)", background: "var(--surface-raised)", border: "1px solid var(--line)" }}>
                  <feature.icon className="w-6 h-6" style={{ color: "var(--long)" }} />
                </div>
                <h3 className="font-mono text-lg font-medium mb-2" style={{ color: "var(--text-hi)" }}>
                  {feature.title}
                </h3>
                <p className="text-sm leading-relaxed" style={{ color: "var(--text-mid)" }}>
                  {feature.desc}
                </p>
              </motion.div>
            ))}
          </div>
        </motion.section>

        {/* Footer CTA */}
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 2.2, ease: [0.23, 1, 0.32, 1] }}
          className="mt-32 text-center"
        >
          <p className="label mb-4" style={{ color: "var(--text-lo)" }}>
            Gata să tranzacționezi ca un profesionist?
          </p>
          <a
            href="/dashboard"
            className="inline-flex items-center gap-2 px-8 py-4 font-mono text-sm font-medium tracking-wider uppercase"
            style={{
              background: "var(--text-hi)",
              color: "var(--surface-void)",
              borderRadius: "var(--r-panel)",
            }}
          >
            Accesează Consola
            <ArrowRight className="w-4 h-4" aria-hidden="true" />
          </a>
        </motion.div>
      </main>

      {/* Scroll indicator */}
      <motion.div
        animate={{ y: [0, 10, 0] }}
        transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
        className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2"
        style={{ color: "var(--text-lo)" }}
      >
        <span className="label">Scroll</span>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 5v14M19 12l-7 7-7-7" />
        </svg>
      </motion.div>
    </div>
  );
}

/* Mini sparkline component */
function Sparkline({ data, color }: { data: number[]; color: string }) {
  const width = 100;
  const height = 48;
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - (v / 100) * height;
    return `${x},${y}`;
  }).join(" ");

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="w-full h-full">
      <defs>
        <linearGradient id="sparkline-gradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.6" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path
        d={`M${points}`}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ filter: "drop-shadow(0 0 4px currentColor)" }}
      />
      <path
        d={`M${points} L${width},${height} L0,${height} Z`}
        fill="url(#sparkline-gradient)"
      />
    </svg>
  );
}

function generateSparklineData(trend: "up" | "down"): number[] {
  const base = trend === "up" ? 45 : 55;
  const data = [base];
  for (let i = 1; i < 20; i++) {
    const drift = trend === "up" ? 1.5 : -1.5;
    const noise = (Math.random() - 0.5) * 8;
    const next = Math.max(5, Math.min(95, data[i - 1] + drift + noise));
    data.push(next);
  }
  return data;
}

export default LandingHero;