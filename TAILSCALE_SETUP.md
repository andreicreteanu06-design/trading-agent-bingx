# Acces de oriunde — Tailscale Setup

De ce **NU** port-forwarding:
- `app/server.py` nu are autentificare (scris în docstring). Oricine găsește portul poartă poate:
  - Reseta kill-switch-ul
  - Porni scanări forțate
  - Citi echity, poziții, semnale
- BingX API keys rămân pe PC, dar controlul agentului e expus

**Tailscale** creează o rețea privată criptată (WireGuard) între PC și telefon. Zero porturi deschise pe router. Zero configurare firewall. Gratuit pentru uz personal.

---

## 1. Pe PC (Windows)

```powershell
# Opțiunea A: Winget (recomandat)
winget install Tailscale.Tailscale

# Opțiunea B: Descarcă de pe https://tailscale.com/download/windows
```

După instalare:
1. Tailscale pornește automat în system tray
2. Click dreapta → **Log in...**
3. Alege metoda (Google, GitHub, Microsoft, email)
4. După login, PC-ul primește un IP Tailscale (ex: `100.x.y.z`)
5. Verifică: `tailscale ip` în PowerShell → arată IP-ul tău `100.x.y.z`

---

## 2. Pe Telefon (Android / iOS)

1. Instalează **Tailscale** din Play Store / App Store
2. Deschide aplicația → **Log in**
3. **Acelasi cont** ca pe PC (Google/GitHub/Microsoft/email)
4. Telefonul primește propriul IP `100.x.y.z` în aceeași rețea

---

## 3. Verifică conectivitatea

Pe telefon, deschide browserul și accesează:
```
http://<IP-PC-TAILSCALE>:3000
```

Exemplu: `http://100.87.42.15:3000`

Dashboard-ul trebuie să încarce. Dacă nu:
- Verifică că `RunAlways.ps1` rulează pe PC (Task Manager → powershell.exe)
- Verifică log-urile: `logs\launcher.log`, `logs\nextjs.log`
- Testează local pe PC: `http://127.0.0.1:3000` (Next.js) și `http://127.0.0.1:8420/api/status` (Python)

---

## 4. URL-ul tău permanent

IP-ul Tailscale **nu se schimbă** între reporniri. Salvează-l ca bookmark pe telefon:
```
http://100.x.y.z:3000
```

Funcționează:
- De pe WiFi acasă
- De pe 4G/5G oriunde
- De pe orice rețea, fără VPN suplimentar

---

## 5. Securitate suplimentară (opțional)

### MagicDNS (nume în loc de IP)
În Tailscale admin console (https://login.tailscale.com/admin/machines):
- Activează **MagicDNS**
- Setează un nume: `bingx-agent`
- Accesezi: `http://bingx-agent:3000` de pe orice dispozitiv în tailnet

### ACL Tags (restricționează cine vede ce)
În admin console → **Access Controls** → **ACL Tags**:
```json
{
  "tagOwners": {
    "tag:dashboard": ["autogroup:member"]
  },
  "acls": [
    {"action": "accept", "src": ["tag:dashboard"], "dst": ["tag:dashboard:3000"]}
  ]
}
```
Apoi pe PC: `tailscale set --advertise-tags=tag:dashboard` (restartă Tailscale)

---

## 6. Troubleshooting

| Problemă | Cauză / Fix |
|---|---|
| "This site can't be reached" pe telefon | PC-ul e în sleep / `RunAlways.ps1` nu rulează / Tailscale oprit pe PC |
| Dashboard încărcă dar `/api/status` e 500 | Python API mort — verifică `logs\python-api.log` |
| "Connection refused" pe `127.0.0.1:8420` | Python API nu a pornit încă — așteaptă 10-15 sec după boot |
| IP Tailscale nu apare | `tailscale up` în PowerShell (re-autentificare) |
| Next.js build errors la pornire | Rulează `cd web && npm run build` manual o dată |

---

## 7. Comenzi utile

```powershell
# Verifică status Tailscale
tailscale status

# IP-ul tău Tailscale
tailscale ip

# Repornește serviciul Tailscale
Restart-Service Tailscale

# Vezi mașinile în tailnet
tailscale status --peers

# Logs launcher
Get-Content logs\launcher.log -Tail 30 -Wait
```

---

## 8. Recap — ce ai acum

| Componentă | Port | Bind | Accesibil |
|---|---|---|---|
| Python API | 8420 | 127.0.0.1 | Doar local (Next.js proxy) |
| Next.js (prod) | 3000 | 0.0.0.0 | **Doar prin Tailscale** |
| Router | — | — | **Niciun port deschis** |

**Rezultat:** Dashboard-ul tău e accesibil de pe telefon, de la cafenea, din altă țară — prin rețeaua privată Tailscale. Nimic expus pe internet public.