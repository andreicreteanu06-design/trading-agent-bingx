# Design brief: Agent BingX console

Scris inainte de rescrierea UI. Sursa de adevar pentru redesign.
De citit la inceputul sesiunii in care se construieste, ca sa nu se re-deriveze nimic.

## 0. Design Read

Consola de operatiuni de trading pentru un singur operator expert, care o deschide
de zeci de ori pe zi. Limbaj de instrument de precizie (aerospace HUD / Braun /
Teenage Engineering), nu landing page. Stack: Tailwind v4 + Motion (`motion/react`),
3D concentrat intr-un singur element care afiseaza date reale.

## 1. Dials

| Dial | Valoare | De ce |
|---|---|---|
| DESIGN_VARIANCE | 5 | Dashboard: structura trebuie sa fie previzibila. Varianta se cheltuie intr-un singur loc. |
| MOTION_INTENSITY | 4 | Suprafata de frecventa mare. Bugetul de miscare merge pe date, nu pe cromatica. |
| VISUAL_DENSITY | 7 | Consola de trading. Dens e corect. |

## 2. Scope: ce skill se aplica si ce nu

`taste-skill` declara explicit dashboards OUT OF SCOPE (sectiunea 13). Din el pastram
doar interdictiile anti-slop: fara em dash, fara puncte de status decorative, fara
screenshot-uri false din div-uri, fara etichete de versiune, eyebrow rationat.
NU aplicam regulile lui de structura de landing page.

Driverele primare sunt `ui-ux-pro-max` (reguli 1-3 CRITICAL/HIGH) si `emil-design-eng`
(framework-ul de animatie pe frecventa).

## 3. Tensiunea centrala si rezolvarea ei

`emil-design-eng`: ce vezi de 100+ ori pe zi nu se animeaza niciodata.
Cererea utilizatorului: "sa arate ca o aplicatie vie".

Rezolvare: **bugetul de animatie merge pe date, nu pe chrome.** Nimic nu se misca
pentru ca e frumos. Se misca doar ce s-a schimbat: un pret, un scor, un scan care
ruleaza, un semnal care intra. Aplicatia e vie pentru ca datele ei sunt vii, nu
pentru ca decoratiile se misca. Asta e si singura definitie de "viu" care rezista
la a 50-a deschidere din zi.

## 4. Culoare

Problema actuala: paleta e GitHub-dark generic (`#0b0e14`, `#4f8cff`, `#3fb950`,
`#ff5c5c`). Pe o consola de trading verde si rosu sunt **culori semantice de date**
(long/short, profit/pierdere). Daca albastru e si el accent de chrome, ai trei culori
saturate care concureaza si ochiul nu mai distinge "asta e data" de "asta e decor".

Regula noua: **singurele culori saturate de pe ecran sunt cele care poarta sens de piata.**
Chrome-ul devine complet acromatic. Butonul primar e text deschis pe negru (inversat),
nu albastru: contrast mai mare si scapam complet de tell-ul "AI blue glow".

```css
--surface-void:   #08090b;  /* corpul consolei */
--surface-rail:   #0e1013;  /* panou */
--surface-raised: #14171c;  /* celula interioara */
--line:           #1e2228;  /* hairline */

--text-hi:  #e8eaed;
--text-mid: #9aa1ab;
--text-lo:  #656c76;

/* date, singurele saturate */
--long:  #2ee6a8;  /* mint rece, citeste ca readout de instrument */
--short: #ff4d5e;
--warn:  #f5a524;
```

Fara accent cromatic de chrome. Zero gradient decorativ. Zero glow.

## 5. Tipografie

Geist Sans + Geist Mono raman (deja incarcate prin `next/font`, nu sunt tell in context
de consola). Ce lipseste si conteaza:

- `font-variant-numeric: tabular-nums` pe **toate** cifrele. Acum preturile isi schimba
  latimea la fiecare poll de 3s si randul tresare. Regula `number-tabular` din ui-ux-pro-max.
- Geist Mono pentru orice numar, simbol sau timestamp.
- Rol nou: micro-eticheta de instrument, 10px / uppercase / `letter-spacing: 0.14em` / `--text-lo`.
- Scala: 10 / 12 / 14 / 18 / 24 / 32.

## 6. Layout

Defectul cel mai mare din UI-ul actual: scena Spline ocupa 420px in capul unei pagini
deschise de zeci de ori pe zi si impinge semnalele sub fold. Plus runtime WebGL de ~1MB
pe o pagina care oricum face polling. Decorativ, pe cea mai scumpa suprafata din aplicatie.

**Spline iese din calea critica.** Se poate reactiva cu o linie daca vrei.

Ordine noua, dupa valoare pentru operator:
1. Rail de sus: identitate, stare conexiune, kill-switch, buton scan. Sticky.
2. Semnale (ce ai venit sa vezi).
3. Instrumente de risc + stare sistem.
4. Istoric.
5. Backtest (cu verdictul negativ pastrat vizibil, vezi sectiunea 9).

## 7. Signature: "Risk Envelope"

Un gauge CSS-3D (`transform-style: preserve-3d`) in rail-ul de sus care randeaza starea
kill-switch-ului ca obiect fizic: trades-azi, pierderi consecutive si drawdown fata de
peak, ca trei inele suprapuse inclinate in spatiu. Se inclina cateva grade la miscarea
cursorului, pe spring, prin `useMotionValue` (niciodata `useState`, ar re-randa arborele
la fiecare pixel).

Este 3D, este futurist, este viu, si fiecare pixel din el e data. Zero WebGL, ~2KB.
Asta e si raspunsul la "foarte bine optimizata".

## 8. Material si adancime (premium 3D fara WebGL)

Motivul real pentru care UI-ul actual arata plat: `shadow-2xl shadow-black/20 backdrop-blur`
pus aleator, fara sursa de lumina consistenta.

Scala de elevatie unica, lumina din acelasi loc peste tot:

```css
--elev-1: inset 0 1px 0 rgb(255 255 255 / .055),
          0 1px 2px rgb(0 0 0 / .6);
--elev-2: inset 0 1px 0 rgb(255 255 255 / .07),
          0 1px 2px rgb(0 0 0 / .6),
          0 8px 24px -8px rgb(0 0 0 / .7);
```

Scala de raze blocata (acum sunt amestecate `lg`, `xl`, `2xl`, `[2rem]`, `full`):
panou 14px, celula interioara 8px, pill full. Nimic altceva.

## 9. Buget de miscare

| Element | Frecventa | Decizie |
|---|---|---|
| Header, panouri, chrome | fiecare load | fara animatie |
| Numar care se schimba | la 3s | flash-on-change 320ms. Asta e "viu". |
| Scan in curs | ocazional | linie de progres reala, sweep |
| Semnal nou intrat | rar | intrare stagger 40ms/item, aici merge delight-ul |
| Inclinare Risk Envelope | hover continuu | spring pe motionValue |
| Kill-switch declansat | foarte rar | tare, imposibil de ratat |

Curbe (emil):
```css
--ease-out:    cubic-bezier(.23, 1, .32, 1);
--ease-in-out: cubic-bezier(.77, 0, .175, 1);
```
Sub 300ms pentru UI. `scale(.97)` pe `:active`. Niciodata din `scale(0)`.
Doar `transform` si `opacity`. `prefers-reduced-motion` respectat.

## 10. Optimizari reale (nu declarate)

1. Spline scos din calea critica: ~1MB runtime + context WebGL pe o pagina care face polling.
2. **Bug real de poll:** `useEffect` depinde de `[status?.scanning]`, deci intervalul e
   distrus si recreat la fiecare comutare, iar `wasScanning` inchide peste un `status` vechi.
   Inlocuit cu poller pe ref + interval adaptiv: 3s in timpul scanarii, 12s in repaus.
   Taie apelurile API in repaus de ~4x.
3. `tabular-nums`: opreste reflow-ul la fiecare poll.
4. Doar `transform`/`opacity` in animatii.
5. `content-visibility: auto` pe tabelele lungi.

## 11. Interdictii (verificare finala)

- Fara em dash nicaieri in text vizibil.
- Fara puncte de status decorative pe langa text care spune deja acelasi lucru.
- Fara emoji ca iconita. Un singur set de iconite, stroke unic.
- Fara glow, fara gradient de accent, fara sticla decorativa.
- Contrast: 4.5:1 text normal, 3:1 text mare si glife UI.
- Tinta de tap minim 44x44.
- Culoarea nu e niciodata singurul purtator de sens (long/short au si eticheta, nu doar verde/rosu).

## 12. "Ultrarealist": ce inseamna concret

Directiva adaugata de utilizator dupa scrierea initiala. Nu inseamna mai multa culoare
sau mai mult glow. Inseamna ca panourile trebuie sa se citeasca drept **hardware fizic**:

- O singura sursa de lumina, de sus, consistenta pe tot ecranul. Highlight-ul de 1px de
  sus si umbra de dedesubt nu sunt decor, sunt ce face o suprafata sa para obiect.
- Margini care se comporta ca muchii: hairline mai deschis sus, mai inchis jos.
- Gauge-ul din sectiunea 7 e piesa centrala de realism: obiect sub sticla, cu paralaxa
  reala la miscarea cursorului.
- Cifrele se comporta ca un readout de instrument: tabular, cu flash scurt la schimbare,
  fara reflow.

Ce ramane interzis, pentru ca strica realismul in loc sa-l creeze: glow neon, gradient
de accent, glassmorphism decorativ, umbre pure negre fara nuanta din fundal.

## 13. Componente din 21st.dev

`getlayers` iese din plan: cere abonament platit. Se lucreaza doar cu `21st`.

Componentele se aleg ca sa incapa peste tokenii de mai sus, nu invers: paleta, scala de
elevatie si scala de raze din acest document bat orice default al componentei importate.

Candidati utili: tabel de date dens, sparkline/mini-chart, ticker de numere animat,
progress determinat, gauge/radial.
De evitat: orice hero, orice card cu gradient, orice glassmorphism.
