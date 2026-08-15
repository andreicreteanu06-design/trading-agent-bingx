# Graph Report - trading-agent-bingx  (2026-08-14)

## Corpus Check
- Corpus is ~26,742 words - fits in a single context window. You may not need a graph.

## Summary
- 440 nodes · 723 edges · 26 communities (23 shown, 3 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 50 edges (avg confidence: 0.68)
- Token cost: 65,610 input · 0 output

## Community Hubs (Navigation)
- Scanner Orchestration, Claude Analysis & Telegram Alerts
- Vanilla HTML Dashboard & Backend REST Contract
- Next.js Dashboard UI (Spline, Spotlight, shadcn Cards)
- News Blackout Windows & Trade Manager
- Risk Engine, Signal Sizing & BingX Client Surface
- Python HTTP API Server & AgentService
- TypeScript Compiler Configuration
- Technical Indicators (EMA/ATR/ADX/RSI) & Signal Builder
- Frontend Dev Tooling (ESLint, Tailwind, Types)
- Backtest Engine & Reporting
- Frontend Runtime Dependencies
- BingX CCXT Exchange Client
- Central Risk & Strategy Configuration
- Next.js Root Layout & Fonts
- ESLint Config
- Next.js Config & API Rewrites
- PostCSS Config

## God Nodes (most connected - your core abstractions)
1. `BingXClient` - 28 edges
2. `KillSwitch` - 24 edges
3. `Scanner` - 20 edges
4. `cn()` - 20 edges
5. `NewsBlackout` - 19 edges
6. `scan()` - 17 edges
7. `compilerOptions` - 16 edges
8. `Signal` - 14 edges
9. `TelegramNotifier` - 13 edges
10. `VolatilityGuard` - 13 edges

## Surprising Connections (you probably didn't know these)
- `Next.js Web App (create-next-app scaffold)` --conceptually_related_to--> `BingX Futures Signal Agent (project overview)`  [AMBIGUOUS]
  web/README.md → README.md
- `Scan Result Status Taxonomy (approved/claude_skip/rejected/no_setup/skipped/error)` --semantically_similar_to--> `Claude Can Only Brake (veto-only LLM role)`  [INFERRED] [semantically similar]
  app/index.html → README.md
- `diagnose.py (explains why no signal exists now)` --semantically_similar_to--> `Scan Result Status Taxonomy (approved/claude_skip/rejected/no_setup/skipped/error)`  [INFERRED] [semantically similar]
  README.md → app/index.html
- `Next.js Web App (create-next-app scaffold)` --semantically_similar_to--> `Agent BingX Dashboard (single-file vanilla HTML/JS UI)`  [INFERRED] [semantically similar]
  web/README.md → app/index.html
- `Scanner` --uses--> `ClaudeAnalyzer`  [INFERRED]
  core/scanner.py → ai/claude_analyzer.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Dashboard data refresh flow (poll status, then reload panels)** — app_index_tick, app_index_api, app_index_renderstatus, app_index_loadscan, app_index_loadhistory, app_index_loadbacktest [EXTRACTED 1.00]
- **Pre-trade risk gating chain (every signal must pass all gates)** — readme_risk_engine, readme_kill_switch, readme_news_blackout, readme_volatility_guard, readme_claude_can_only_brake, readme_tradingview_webhook [EXTRACTED 1.00]
- **Negative-edge evidence chain (backtest result, cause hypothesis, surfaced warning)** — readme_backtest_engine, readme_negative_edge_finding, readme_ema50_pullback_crowding_hypothesis, app_index_loadbacktest [INFERRED 0.85]

## Communities (26 total, 3 thin omitted)

### Community 0 - "Scanner Orchestration, Claude Analysis & Telegram Alerts"
Cohesion: 0.06
Nodes (33): ClaudeAnalyzer, _parse_json(), Any, Stratul de analiza cu Claude. Rolul lui Claude aici este ingust si deliberat:…, Parsare toleranta: accepta si raspunsuri invelite in ```json ... ```., Notificari Telegram. Optional - daca lipsesc credentialele, tace elegant., TelegramNotifier, Any (+25 more)

### Community 1 - "Vanilla HTML Dashboard & Backend REST Contract"
Cohesion: 0.06
Nodes (49): api() fetch wrapper, Backend REST Contract (/api/status, /api/scan, /api/history, /api/backtest, /api/auto), Dark Terminal Design System (CSS variables, mono tabular numerals), Agent BingX Dashboard (single-file vanilla HTML/JS UI), esc() HTML entity escaping helper, fmt() ro-RO locale number formatter, loadBacktest() backtest report table, loadHistory() signal history table (+41 more)

### Community 2 - "Next.js Dashboard UI (Spline, Spotlight, shadcn Cards)"
Cohesion: 0.08
Nodes (33): AgentDashboard(), Alert(), AnalysisData, BacktestReport, Dot(), Empty(), fmt(), fmtPrice() (+25 more)

### Community 3 - "News Blackout Windows & Trade Manager"
Cohesion: 0.10
Nodes (18): datetime, BlackoutConfig, BlackoutWindow, NewsBlackout, Filtru de stiri - implementat ca BLACKOUT, nu ca sursa de semnal. De ce nu ca…, Reguli de recurenta pentru evenimentele macro care misca crypto. Sunt…, Returneaza (permis, motive). permis=False inseamna: NU deschide pozitie acum., Cand se termina blackout-ul curent. None daca nu suntem in blackout. (+10 more)

### Community 4 - "Risk Engine, Signal Sizing & BingX Client Surface"
Cohesion: 0.08
Nodes (20): Wrapper peste CCXT pentru BingX perpetual futures (USDT-M). Tot ce tine de…, _estimate_liquidation(), evaluate(), _maintenance_margin_rate(), Any, Risk engine determinist. Acesta este stratul care te tine in viata. Nu contine…, Pret de lichidare aproximativ pentru izolat, fara PnL-ul altor pozitii.…, Transforma un Signal intr-un SizedTrade validat. Nu trimite niciun ordin - doar… (+12 more)

### Community 5 - "Python HTTP API Server & AgentService"
Cohesion: 0.09
Nodes (15): AgentService, Handler, _load_backtests(), _local_ip(), main(), BaseHTTPRequestHandler, Dashboard web local pentru agent. python -m app.server # doar pe acest PC…, Rapoartele de backtest salvate, fara lista completa de trades. (+7 more)

### Community 6 - "TypeScript Compiler Configuration"
Cohesion: 0.07
Nodes (28): dom, dom.iterable, esnext, **/*.mts, .next/dev/types/**/*.ts, next-env.d.ts, .next/types/**/*.ts, node_modules (+20 more)

### Community 7 - "Technical Indicators (EMA/ATR/ADX/RSI) & Signal Builder"
Cohesion: 0.14
Nodes (26): Side, adx(), atr(), ema(), enrich(), DataFrame, Series, Indicatori tehnici, implementati in pandas curat (fara ta-lib, care are nevoie… (+18 more)

### Community 8 - "Frontend Dev Tooling (ESLint, Tailwind, Types)"
Cohesion: 0.08
Nodes (25): eslint, eslint-config-next, tailwindcss, @tailwindcss/postcss, @types/node, @types/react, @types/react-dom, typescript (+17 more)

### Community 9 - "Backtest Engine & Reporting"
Cohesion: 0.13
Nodes (13): Backtester, BacktestReport, ClosedTrade, Any, DataFrame, Series, Backtester event-driven, fara lookahead. Regulile de simulare - cele care fac…, Ruleaza strategia pe date istorice. NU are nevoie de chei API - foloseste doar… (+5 more)

### Community 10 - "Frontend Runtime Dependencies"
Cohesion: 0.11
Nodes (19): clsx, framer-motion, lucide-react, next, react, react-dom, @splinetool/react-spline, @splinetool/runtime (+11 more)

### Community 11 - "BingX CCXT Exchange Client"
Cohesion: 0.15
Nodes (7): BingXClient, Any, DataFrame, Echity total in USDT pe contul de futures. None daca nu avem chei., Pozitiile deschise, normalizate. Lista goala daca nu avem chei., Returneaza un DataFrame OHLCV, cu ultima lumanare (inca in formare) eliminata.…, Aduce `total` lumanari paginand inapoi in timp. BingX limiteaza raspunsul…

### Community 12 - "Central Risk & Strategy Configuration"
Cohesion: 0.22
Nodes (8): AppConfig, MarketConfig, Configuratia centrala a agentului. Toate limitele de risc de aici sunt HARD…, Parametrii indicatorilor. Modifica-i doar dupa backtest, nu dupa intuitie., Ce scanam si pe ce timeframe-uri., Parametrii de risc. Acestea sunt cele mai importante numere din tot proiectul.…, RiskConfig, StrategyConfig

### Community 13 - "Next.js Root Layout & Fonts"
Cohesion: 0.40
Nodes (3): geistMono, geistSans, metadata

## Ambiguous Edges - Review These
- `BingX Futures Signal Agent (project overview)` → `Next.js Web App (create-next-app scaffold)`  [AMBIGUOUS]
  web/README.md · relation: conceptually_related_to

## Knowledge Gaps
- **78 isolated node(s):** `AppConfig`, `eslintConfig`, `nextConfig`, `name`, `version` (+73 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `BingX Futures Signal Agent (project overview)` and `Next.js Web App (create-next-app scaffold)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `BingXClient` connect `Community 11` to `Community 0`, `Community 4`, `Community 5`, `Community 7`, `Community 9`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Why does `Scanner` connect `Community 5` to `Community 0`, `Community 3`, `Community 11`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `KillSwitch` connect `Community 0` to `Community 3`, `Community 5`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `BingXClient` (e.g. with `Scanner` and `ScanResult`) actually correct?**
  _`BingXClient` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `KillSwitch` (e.g. with `Scanner` and `ScanResult`) actually correct?**
  _`KillSwitch` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `Scanner` (e.g. with `AgentService` and `Handler`) actually correct?**
  _`Scanner` has 9 INFERRED edges - model-reasoned connections that need verification._