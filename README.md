# gu-monitor

Controlla la Gazzetta Ufficiale 4ª Serie Speciale (Concorsi ed Esami) e ti scrive su WhatsApp i bandi nuovi che contengono le tue parole chiave.

## Installazione
```bash
pip install -r requirements.txt     # serve Python 3.11+
```
Modifica `config.toml` (parole chiave, esclusioni, numero).

## WhatsApp: scegli un provider
**CallMeBot (gratis, uso personale)**
1. Salva il contatto +34 644 51 95 23 e invia: `I allow callmebot to send me messages`
2. Ricevi una apikey; esportala: `export CALLMEBOT_APIKEY=xxxx`

**Twilio (piu' robusto)**: crea account, attiva la WhatsApp Sandbox, poi
`export TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=...` e imposta `provider = "twilio"`.

## Primo avvio
```bash
python monitor_gu.py --dry-run         # vedi cosa troverebbe (non invia nulla)
python monitor_gu.py --test-whatsapp   # verifica l'invio
python monitor_gu.py --first-run       # segna i bandi attuali come gia' visti
python monitor_gu.py                   # da ora in poi notifica solo i nuovi
```

## Pianificazione
- **GitHub Actions (consigliato, gratis)**: carica la cartella su un repo privato, aggiungi i secret (`CALLMEBOT_APIKEY` o quelli Twilio) e lancia una volta `--first-run` in locale committando `seen.json`. Il workflow in `.github/workflows/monitor.yml` gira martedi/mercoledi/venerdi/sabato.
- **Cron su un tuo PC/Raspberry**: `30 9 * * 2,3,5,6  cd /percorso/gu-monitor && /usr/bin/python3 monitor_gu.py`

## Note
- La Gazzetta esce martedi e venerdi (non festivi).
- Se lo script dice "nessuna Gazzetta trovata" il sito ha cambiato HTML: va aggiornato il parser (`list_issues` / `parse_issue`).
- I bandi sono identificati dal codice redazionale (es. `26E01234`), cosi' non ricevi mai duplicati.
