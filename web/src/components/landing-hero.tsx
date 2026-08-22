"use client";

/**
 * Landing Hero - ZiroBuildHero inspired design
 * Video background with robot animation, animated text, crypto logos, interactive charts
 */

import {
  motion,
  useMotionValue,
  useTransform,
  useSpring,
  useReducedMotion,
} from "framer-motion";
import { useEffect } from "react";
import { ArrowRight, Sparkles, TrendingUp, Cpu, Shield } from "lucide-react";

/*
  Marcile BTC/ETH/SOL, luate din svgl.app dar servite local din `public/logos/`.
  Inainte erau incarcate direct de pe svgl.app la runtime: o pagina altfel complet
  statica ajungea sa depinda de un CDN tert, iar daca acela cade sau isi schimba
  caile, stripul se randeaza ca trei casute sparte.
*/
const CRYPTO_LOGOS = {
  BTC: "/logos/btc.svg",
  ETH: "/logos/eth.svg",
  SOL: "/logos/sol.svg",
};

export function LandingHero() {
  const reduceMotion = useReducedMotion();
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);

  const springX = useSpring(mouseX, { stiffness: 100, damping: 12 });
  const springY = useSpring(mouseY, { stiffness: 100, damping: 12 });

  // Parallax pe stratul de adancime. Valorile trebuie legate prin useTransform,
  // nu citite cu .get() intr-un obiect de stil: un `.get()` se evalueaza o
  // singura data, la randare, si dupa aia nu se mai misca nimic. Asta era
  // motivul pentru care parallax-ul "exista" in cod dar nu facea nimic.
  const depthX = useTransform(springX, [-0.5, 0.5], [18, -18]);
  const depthY = useTransform(springY, [-0.5, 0.5], [12, -12]);

  useEffect(() => {
    if (reduceMotion) return;
    const handleMove = (e: MouseEvent) => {
      mouseX.set(e.clientX / window.innerWidth - 0.5);
      mouseY.set(e.clientY / window.innerHeight - 0.5);
    };
    window.addEventListener("mousemove", handleMove, { passive: true });
    return () => window.removeEventListener("mousemove", handleMove);
  }, [mouseX, mouseY, reduceMotion]);

  return (
    <div className="relative min-h-[100dvh] w-full overflow-hidden bg-[var(--surface-void)]">
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

      {/* Strat de adancime: cercurile se deplaseaza cu cursorul (parallax real) */}
      <motion.div
        className="pointer-events-none absolute inset-0 z-0"
        aria-hidden="true"
        style={reduceMotion ? undefined : { x: depthX, y: depthY }}
      >
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
            animate={reduceMotion ? undefined : { rotate: [0, 360] }}
            transition={{
              duration: 40 + i * 10,
              repeat: Infinity,
              ease: "linear",
            }}
          />
        ))}
      </motion.div>

      {/* Main Content */}
      <main className="relative z-10 flex min-h-[100dvh] flex-col items-center justify-center px-6 pt-20 pb-16">
        {/*
          Blocul de titlu. Aici era un gate pe `useState(mounted)` setat dintr-un
          efect, doar ca sa porneasca o tranzitie CSS la montare. Fiecare copil de
          mai jos are deja `initial`/`animate`, deci wrapper-ul dubla intrarea si,
          pe deasupra, forta un al doilea render sincron imediat dupa montare.
        */}
        <div className="text-center max-w-5xl">
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
                className="group"
                whileHover={{ scale: 1.15, y: -4 }}
              >
                <div className="relative w-16 h-16 md:w-20 md:h-20 flex items-center justify-center">
                  {/*
                    `next/image` nu optimizeaza SVG, deci `<img>` e alegerea
                    corecta aici. Latimea si inaltimea sunt declarate ca sa nu
                    sara layout-ul (CLS). Glow-ul verde de dinainte a picat:
                    verdele e culoare cu sens de piata (§4), nu decor pe un logo.
                  */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={url}
                    alt={`${symbol} logo`}
                    width={80}
                    height={80}
                    loading="lazy"
                    className="w-full h-full object-contain"
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
          {/*
            Aici erau "Win Rate 67.3%", "Profit Factor 2.41" si "Max Drawdown
            8.7%", cu sparkline-uri generate din Math.random(). Cifrele erau
            inventate si contraziceau direct verdictul propriu al consolei
            ("expectanta e negativa, nu tranzactiona cu bani reali") si copy-ul
            de doua sectiuni mai jos (-0.2R, 45% WR).

            Le-am inlocuit cu limitele reale din config.py, care sunt oricum
            argumentul mai bun: nu performanta promisa, ci disciplina impusa.
          */}
          <div className="grid gap-6 md:grid-cols-3">
            {[
              {
                label: "Risc per tranzacție",
                value: "1%",
                note: "fix din capital. Levierul rezultă din distanța până la stop, nu invers.",
                icon: Shield,
              },
              {
                label: "Levier maxim",
                value: "5x",
                note: "plafon dur în risk engine. Peste el semnalul e respins, indiferent de scor.",
                icon: Cpu,
              },
              {
                label: "Expectanță backtest",
                value: "−0.2R",
                note: "negativă, și scrie asta pe consolă. De aceea execuția automată e oprită.",
                icon: TrendingUp,
                tone: "short" as const,
              },
            ].map((stat, i) => (
              <motion.div
                key={stat.label}
                initial={{ opacity: 0, y: 24 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-80px" }}
                transition={{ duration: 0.45, delay: i * 0.08, ease: [0.23, 1, 0.32, 1] }}
                className="panel p-6"
              >
                <div className="mb-4 flex items-start justify-between">
                  <stat.icon
                    className="h-5 w-5"
                    strokeWidth={1.5}
                    style={{ color: stat.tone === "short" ? "var(--short)" : "var(--text-mid)" }}
                    aria-hidden="true"
                  />
                  <span className="label" style={{ color: "var(--text-lo)" }}>
                    {stat.label}
                  </span>
                </div>
                <div
                  className="num mb-2 font-mono text-4xl font-medium md:text-5xl"
                  style={{ color: stat.tone === "short" ? "var(--short)" : "var(--text-hi)" }}
                >
                  {stat.value}
                </div>
                <p className="text-sm leading-relaxed" style={{ color: "var(--text-mid)" }}>
                  {stat.note}
                </p>
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
        animate={reduceMotion ? undefined : { y: [0, 10, 0] }}
        transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
        className="absolute bottom-8 left-1/2 flex -translate-x-1/2 flex-col items-center gap-2"
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

/*
 * Sparkline si generateSparklineData au fost sterse odata cu cifrele inventate.
 * Aveau doua defecte care meritau mentionate, ca sa nu reapara:
 *  - toate cele trei instante foloseau acelasi `id="sparkline-gradient"`, deci
 *    browserul rezolva `url(#...)` la primul si toate trei se umpleau cu
 *    aceeasi culoare;
 *  - datele veneau din Math.random() apelat in timpul randarii, deci serverul
 *    si clientul desenau curbe diferite - hydration mismatch garantat.
 * Daca revine un grafic aici, trebuie sa arate date reale din /api/backtest.
 */

export default LandingHero;