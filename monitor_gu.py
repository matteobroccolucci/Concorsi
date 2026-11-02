#!/usr/bin/env python3
"""
Monitor della Gazzetta Ufficiale - 4a Serie Speciale "Concorsi ed Esami".

Scarica l'elenco delle Gazzette degli ultimi 30 giorni, legge i bandi di ogni
numero, filtra quelli che contengono le tue parole chiave (config.toml) e invia
su WhatsApp solo i bandi NUOVI (mai notificati prima).

Uso:
    python monitor_gu.py                # esecuzione normale
    python monitor_gu.py --dry-run      # stampa a video, non invia e non salva lo stato
    python monitor_gu.py --first-run    # segna tutto come gia' visto senza inviare
    python monitor_gu.py --test-whatsapp  # invia un messaggio di prova
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.gazzettaufficiale.it"
LIST_URL = f"{BASE}/30giorni/concorsi"
HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.toml"
STATE_PATH = HERE / "seen.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; gu-monitor/1.0; uso personale)"}
CODICE_RE = re.compile(r"codiceRedazionale=([0-9A-Z]+)")


# --------------------------------------------------------------------------- #
# Configurazione e stato
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def load_seen() -> set[str]:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_PATH.write_text(json.dumps(sorted(seen), indent=0), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #
def get(url: str) -> BeautifulSoup:
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as e:  # retry con backoff
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Impossibile scaricare {url}: {last_err}")


def list_issues() -> list[tuple[str, str]]:
    """Restituisce [(etichetta, url)] per ogni Gazzetta degli ultimi 30 giorni."""
    soup = get(LIST_URL)
    issues, seen_urls = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "caricaDettaglio" in href and "dataPubblicazioneGazzetta" in href:
            url = urljoin(BASE, href)
            if url not in seen_urls:
                seen_urls.add(url)
                issues.append((" ".join(a.get_text(" ", strip=True).split()), url))
    return issues


def parse_issue(label: str, url: str) -> list[dict]:
    """Estrae i bandi di una Gazzetta: codice, titolo, ente, link."""
    soup = get(url)
    items: dict[str, dict] = {}
    ente = ""
    # Scorre il documento in ordine: aggiorna l'ente quando incontra un'intestazione,
    # registra un bando quando incontra un link con codiceRedazionale.
    for el in soup.find_all(["span", "div", "h2", "h3", "h4", "strong", "a"]):
        classes = " ".join(el.get("class", []))
        if el.name != "a" and re.search(r"emettitore|ente|rubrica", classes, re.I):
            txt = " ".join(el.get_text(" ", strip=True).split())
            if txt:
                ente = txt
            continue
        if el.name == "a" and el.get("href"):
            m = CODICE_RE.search(el["href"])
            if not m:
                continue
            code = m.group(1)
            title = " ".join(el.get_text(" ", strip=True).split())
            if not title or code in items:
                continue
            items[code] = {
                "code": code,
                "title": title,
                "ente": ente,
                "url": urljoin(BASE, el["href"]),
                "issue": label,
            }
    return list(items.values())


# --------------------------------------------------------------------------- #
# Filtro
# --------------------------------------------------------------------------- #
_TEXT_CACHE: dict[str, str] = {}


def fetch_text(url: str) -> str:
    """Testo completo del bando (con cache), per cercare specialita'/luogo non presenti nel titolo."""
    if url not in _TEXT_CACHE:
        try:
            _TEXT_CACHE[url] = " ".join(get(url).get_text(" ", strip=True).split()).lower()
        except RuntimeError:
            _TEXT_CACHE[url] = ""
        time.sleep(1)
    return _TEXT_CACHE[url]


def _eval_groups(text: str, groups: list[list[str]]) -> list[str]:
    """Ogni gruppo deve avere almeno una parola presente (AND tra gruppi, OR dentro il gruppo).
    Ritorna le parole trovate, oppure [] se un gruppo non e' soddisfatto."""
    found = []
    for group in groups:
        hit = [k for k in group if k in text]
        if not hit:
            return []
        found.append(hit[0])
    return found



def matches(item: dict, cfg: dict) -> list[str]:
    """Cerca le specialità nel titolo e, se necessario, nel testo integrale."""
    flt = cfg.get("filtri", {})

    # Titolo e nome dell'ente
    text = f"{item['title']} {item['ente']}".lower()

    # Esclusioni basate sul titolo e sull'ente
    exclude = [k.lower() for k in flt.get("escludi", [])]
    if any(k in text for k in exclude):
        return []

    # Compatibilità con il vecchio filtro a gruppi
    groups = [
        [k.lower() for k in g]
        for g in flt.get("gruppi_and", [])
    ]

    if groups:
        found = _eval_groups(text, groups)
        if found:
            return found

    else:
        # Nuovo filtro nazionale per parole chiave
        include = [
            k.lower()
            for k in flt.get("parole_chiave", [])
        ]

        if not include:
            return ["*"]

        found = [k for k in include if k in text]
        if found:
            return found

    # Se non trova corrispondenze nel titolo,
    # controlla il testo integrale del bando
    if flt.get("cerca_nel_testo", False):
        full_text = f"{text} {fetch_text(item['url'])}"

        if groups:
            return _eval_groups(full_text, groups)

        return [
            k for k in include
            if k in full_text
        ]

    return []


    # Modalita' a gruppi: [[specialita...], [luogo...]] -> serve un match in OGNI gruppo
    groups = [[k.lower() for k in g] for g in flt.get("gruppi_and", [])]
    if groups:
        found = _eval_groups(text, groups)
        if found:
            return found
        # Titolo/ente non bastano (es. "dirigente medico, varie discipline"):
        # solo per i bandi che passano il pre-filtro, leggo il testo completo.
        pre = [k.lower() for k in flt.get("prefiltro_testo", [])]
        if flt.get("cerca_nel_testo", False) and any(k in text for k in pre):
            full = f"{text} {fetch_text(item['url'])}"
            return _eval_groups(full, groups)
        return []

    include = [k.lower() for k in flt.get("parole_chiave", [])]
    if not include:  # nessun filtro = tutto
        return ["*"]
    return [k for k in include if k in text]


# --------------------------------------------------------------------------- #
# WhatsApp
# --------------------------------------------------------------------------- #
def send_whatsapp(cfg: dict, body: str) -> None:
    wa = cfg["whatsapp"]
    provider = wa.get("provider", "callmebot")

    if provider == "twilio":
        sid = os.environ["TWILIO_ACCOUNT_SID"]
        token = os.environ["TWILIO_AUTH_TOKEN"]
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={
                "From": f"whatsapp:{wa['twilio_from']}",
                "To": f"whatsapp:{wa['numero']}",
                "Body": body,
            },
            timeout=30,
        )
    elif provider == "callmebot":
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={
                "phone": wa["numero"].lstrip("+"),
                "text": body,
                "apikey": os.environ["CALLMEBOT_APIKEY"],
            },
            timeout=30,
        )
    else:
        raise ValueError(f"provider WhatsApp sconosciuto: {provider}")

    if r.status_code >= 300:
        raise RuntimeError(f"Invio WhatsApp fallito ({r.status_code}): {r.text[:300]}")

def send_telegram(body: str) -> None:
    """Invia una notifica Telegram."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": body,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    r.raise_for_status()

    result = r.json()
    if not result.get("ok"):
        raise RuntimeError(f"Invio Telegram fallito: {result}")


def format_messages(found: list[tuple[dict, list[str]]], max_len: int = 1500) -> list[str]:
    """Raggruppa i bandi in messaggi non troppo lunghi."""
    blocks = []
    for item, kws in found:
        kw = "" if kws == ["*"] else f"\n🔎 {', '.join(kws)}"
        blocks.append(
            f"📌 {item['title']}\n🏛 {item['ente'] or 'n/d'}\n📰 {item['issue']}{kw}\n🔗 {item['url']}"
        )
    messages, current = [], f"📢 Nuovi concorsi in Gazzetta Ufficiale ({len(found)})\n"
    for b in blocks:
        if len(current) + len(b) + 2 > max_len and current.strip():
            messages.append(current.strip())
            current = ""
        current += "\n" + b + "\n"
    if current.strip():
        messages.append(current.strip())
    return messages


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--first-run", action="store_true")
    ap.add_argument("--test-whatsapp", action="store_true")
    args = ap.parse_args()

    cfg = load_config()

    if args.test_whatsapp:
        send_whatsapp(cfg, "✅ gu-monitor: messaggio di prova.")
        print("Messaggio di prova inviato.")
        return 0

    seen = load_seen()
    issues = list_issues()
    if not issues:
        print("ATTENZIONE: nessuna Gazzetta trovata. La struttura della pagina e' cambiata?",
              file=sys.stderr)
        return 2

    found, all_codes = [], set()
    for label, url in issues:
        items = parse_issue(label, url)
        for it in items:
            all_codes.add(it["code"])
            if it["code"] in seen:
                continue
            kws = matches(it, cfg)
            if kws:
                found.append((it, kws))
        time.sleep(1)  # cortesia verso il server

    print(f"Gazzette lette: {len(issues)} | bandi totali: {len(all_codes)} | "
          f"nuovi che ti interessano: {len(found)}")

    if args.dry_run:
        for it, kws in found:
            print(f"- [{it['issue']}] {it['title']} ({it['ente']}) -> {it['url']}  {kws}")
        return 0

    if args.first_run:
        save_seen(seen | all_codes)
        print("Primo avvio: stato inizializzato, nessun messaggio inviato.")
        return 0

    if found:
        for msg in format_messages(found):
            send_whatsapp(cfg, msg)
            time.sleep(1)

    # Segna come visti TUTTI i bandi letti (anche quelli non di interesse),
    # solo dopo l'invio riuscito.
    save_seen(seen | all_codes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
