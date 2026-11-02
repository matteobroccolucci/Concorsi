
#!/usr/bin/env python3
"""
Monitor Gazzetta Ufficiale - 4a Serie Speciale Concorsi ed Esami.

Ricerca nazionale dei concorsi di Chirurgia Generale:
- Enti sanitari del Lazio;
- altri enti con posti o sedi di lavoro nel Lazio.

Invia notifiche WhatsApp e Telegram per i nuovi bandi.
Invia inoltre un riepilogo Telegram a ogni controllo.
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; gu-monitor/1.0; uso personale)"
}

CODICE_RE = re.compile(r"codiceRedazionale=([0-9A-Z]+)")


# ---------------------------------------------------------------------------
# CONFIGURAZIONE E STATO
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def load_seen() -> set[str]:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_PATH.write_text(
        json.dumps(sorted(seen), indent=0),
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# SCRAPING GAZZETTA UFFICIALE
# ---------------------------------------------------------------------------

def get(url: str) -> BeautifulSoup:
    last_err = None

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")

        except requests.RequestException as error:
            last_err = error
            time.sleep(2 * (attempt + 1))

    raise RuntimeError(
        f"Impossibile scaricare {url}: {last_err}"
    )


def list_issues() -> list[tuple[str, str]]:
    """Restituisce le Gazzette degli ultimi 30 giorni."""

    soup = get(LIST_URL)

    issues = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if (
            "caricaDettaglio" in href
            and "dataPubblicazioneGazzetta" in href
        ):
            url = urljoin(BASE, href)

            if url not in seen_urls:
                seen_urls.add(url)

                label = " ".join(
                    a.get_text(" ", strip=True).split()
                )

                issues.append((label, url))

    return issues


def parse_issue(label: str, url: str) -> list[dict]:
    """Estrae i bandi di una Gazzetta."""

    soup = get(url)

    items = {}
    ente = ""

    for el in soup.find_all(
        ["span", "div", "h2", "h3", "h4", "strong", "a"]
    ):
        classes = " ".join(el.get("class", []))

        if (
            el.name != "a"
            and re.search(r"emettitore|ente|rubrica", classes, re.I)
        ):
            txt = " ".join(
                el.get_text(" ", strip=True).split()
            )

            if txt:
                ente = txt

            continue

        if el.name == "a" and el.get("href"):
            match = CODICE_RE.search(el["href"])

            if not match:
                continue

            code = match.group(1)

            title = " ".join(
                el.get_text(" ", strip=True).split()
            )

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


# ---------------------------------------------------------------------------
# TESTO INTEGRALE DEI BANDI
# ---------------------------------------------------------------------------

_TEXT_CACHE: dict[str, str] = {}


def fetch_text(url: str) -> str:
    """Scarica e memorizza il testo integrale del bando."""

    if url not in _TEXT_CACHE:
        try:
            _TEXT_CACHE[url] = " ".join(
                get(url).get_text(" ", strip=True).split()
            ).lower()

        except RuntimeError:
            _TEXT_CACHE[url] = ""

        time.sleep(1)

    return _TEXT_CACHE[url]


# ---------------------------------------------------------------------------
# FILTRO GEOGRAFICO E SPECIALISTICO
# ---------------------------------------------------------------------------

def matches(item: dict, cfg: dict) -> list[str]:
    """
    Seleziona bandi di Chirurgia Generale:
    1. pubblicati da enti sanitari del Lazio;
    2. oppure con sede di lavoro nel Lazio indicata nel bando.
    """

    flt = cfg.get("filtri", {})

    title = str(item.get("title", ""))
    ente = str(item.get("ente", ""))

    text = f"{title} {ente}".lower()

    # Esclusioni per specialità non pertinenti
    exclude = [
        str(k).lower()
        for k in flt.get("escludi", [])
    ]

    if any(k in text for k in exclude):
        return []

    # Enti sanitari del Lazio e denominazioni alternative
    enti_lazio = [
        "asl roma 1",
        "asl roma 2",
        "asl roma 3",
        "asl roma 4",
        "asl roma 5",
        "asl roma 6",
        "asl rm 1",
        "asl rm 2",
        "asl rm 3",
        "asl rm 4",
        "asl rm 5",
        "asl rm 6",
        "asl di roma 1",
        "asl di roma 2",
        "asl di roma 3",
        "asl di roma 4",
        "asl di roma 5",
        "asl di roma 6",
        "asl frosinone",
        "asl di frosinone",
        "asl latina",
        "asl di latina",
        "asl rieti",
        "asl di rieti",
        "asl viterbo",
        "asl di viterbo",
        "azienda sanitaria locale roma",
        "azienda sanitaria locale di frosinone",
        "azienda sanitaria locale di latina",
        "azienda sanitaria locale di rieti",
        "azienda sanitaria locale di viterbo",
        "azienda ospedaliera san camillo",
        "san camillo forlanini",
        "azienda ospedaliera san giovanni",
        "san giovanni addolorata",
        "policlinico umberto i",
        "policlinico umberto primo",
        "policlinico tor vergata",
        "azienda ospedaliera sant'andrea",
        "azienda ospedaliera sant’andrea",
        "ospedale sant'andrea",
        "ospedale sant’andrea",
        "ifo",
        "istituti fisioterapici ospitalieri",
        "istituto nazionale tumori regina elena",
        "regina elena",
        "spallanzani",
        "ospedale pediatrico bambino gesu",
        "ospedale pediatrico bambino gesù",
        "santa maria goretti",
        "sandro pertini",
        "san filippo neri",
        "sant'eugenio",
        "sant’eugenio",
        "belcolle",
        "santa rosa",
        "san camillo de lellis",
        "fabrizio spaziani",
        "santa scolastica",
        "san paolo di civitavecchia",
    ]

    ente_lazio = any(
        nome in text for nome in enti_lazio
    )

    # Località del Lazio
    localita_lazio = [
        "lazio",
        "roma",
        "frosinone",
        "latina",
        "rieti",
        "viterbo",
        "civitavecchia",
        "tivoli",
        "guidonia",
        "pomezia",
        "anzio",
        "aprilia",
        "cassino",
        "sora",
        "formia",
        "terracina",
        "velletri",
        "colleferro",
        "monterotondo",
        "subiaco",
        "tarquinia",
        "cerveteri",
        "bracciano",
        "nettuno",
        "frascati",
        "marino",
        "albano laziale",
        "fondi",
        "gaeta",
        "minturno",
        "isola del liri",
        "ceccano",
        "alatri",
        "ferentino",
        "anagni",
        "palestrina",
        "ladispoli",
        "fiumicino",
        "capena",
        "ronciglione",
        "montefiascone",
        "cittaducale",
        "poggio mirteto",
    ]

    # Recupera il testo integrale per cercare specialità e località
    full_text = text

    if flt.get("cerca_nel_testo", False):
        full_text += " " + fetch_text(item["url"])

    # Ente laziale oppure località laziale nel testo
    sede_lazio = any(
        loc in full_text for loc in localita_lazio
    )

    if not (ente_lazio or sede_lazio):
        return []

    # Compatibilità con il vecchio filtro a gruppi AND
    groups = [
        [str(k).lower() for k in group]
        for group in flt.get("gruppi_and", [])
    ]

    if groups:
        found = []

        for group in groups:
            hit = [k for k in group if k in full_text]

            if not hit:
                return []

            found.append(hit[0])

        return found

    # Parole chiave della Chirurgia Generale
    include = [
        str(k).lower()
        for k in flt.get("parole_chiave", [])
    ]

    if not include:
        return ["Lazio"]

    return [
        keyword
        for keyword in include
        if keyword in full_text
    ]


# ---------------------------------------------------------------------------
# INVIO WHATSAPP
# ---------------------------------------------------------------------------

def send_whatsapp(cfg: dict, body: str) -> None:

    wa = cfg["whatsapp"]
    provider = wa.get("provider", "callmebot")

    if provider == "twilio":

        sid = os.environ["TWILIO_ACCOUNT_SID"]
        token = os.environ["TWILIO_AUTH_TOKEN"]

        response = requests.post(
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

        response = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={
                "phone": wa["numero"].lstrip("+"),
                "text": body,
                "apikey": os.environ["CALLMEBOT_APIKEY"],
            },
            timeout=30,
        )

    else:
        raise ValueError(
            f"Provider WhatsApp sconosciuto: {provider}"
        )

    if response.status_code >= 300:
        raise RuntimeError(
            f"Invio WhatsApp fallito ({response.status_code}): "
            f"{response.text[:300]}"
        )


# ---------------------------------------------------------------------------
# INVIO TELEGRAM
# ---------------------------------------------------------------------------

def send_telegram(body: str) -> None:

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": body,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Invio Telegram fallito: {result}"
        )


# ---------------------------------------------------------------------------
# FORMATTAZIONE MESSAGGI
# ---------------------------------------------------------------------------

def format_messages(
    found: list[tuple[dict, list[str]]],
    max_len: int = 1500
) -> list[str]:

    blocks = []

    for item, kws in found:

        kw = (
            ""
            if kws == ["*"]
            else f"\n🔎 {', '.join(kws)}"
        )

        blocks.append(
            f"📌 {item['title']}\n"
            f"🏛 {item['ente'] or 'n/d'}\n"
            f"📰 {item['issue']}{kw}\n"
            f"🔗 {item['url']}"
        )

    messages = []
    current = (
        f"📢 Nuovi concorsi di Chirurgia Generale nel Lazio "
        f"({len(found)})\n"
    )

    for block in blocks:

        if len(current) + len(block) + 2 > max_len:
            if current.strip():
                messages.append(current.strip())

            current = ""

        current += "\n" + block + "\n"

    if current.strip():
        messages.append(current.strip())

    return messages


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--first-run", action="store_true")
    parser.add_argument("--test-whatsapp", action="store_true")
    parser.add_argument("--test-telegram", action="store_true")

    args = parser.parse_args()

    cfg = load_config()

    # Test WhatsApp
    if args.test_whatsapp:

        send_whatsapp(
            cfg,
            "✅ gu-monitor: messaggio di prova WhatsApp."
        )

        print("Messaggio WhatsApp inviato.")
        return 0

    # Test Telegram
    if args.test_telegram:

        send_telegram(
            "✅ gu-monitor: messaggio di prova Telegram."
        )

        print("Messaggio Telegram inviato.")
        return 0

    seen = load_seen()

    issues = list_issues()

    if not issues:

        print(
            "ATTENZIONE: nessuna Gazzetta trovata. "
            "La struttura della pagina potrebbe essere cambiata.",
            file=sys.stderr
        )

        return 2

    found = []
    all_codes = set()

    for label, url in issues:

        items = parse_issue(label, url)
                # DIAGNOSTICA: individua bandi pubblicati da enti del Lazio
        enti_da_controllare = [
            "asl roma", "asl rm",
            "asl frosinone", "asl latina",
            "asl rieti", "asl viterbo",
            "azienda sanitaria locale",
            "policlinico umberto",
            "policlinico tor vergata",
            "sant'andrea", "sant’andrea",
            "san camillo", "san giovanni",
            "spallanzani", "ifo",
            "regina elena", "bambino gesu",
            "bambino gesù",
        ]

        for bando in items:
            testo_bando = (
                str(bando.get("title", "")) + " " +
                str(bando.get("ente", ""))
            ).lower()

            if any(nome in testo_bando for nome in enti_da_controllare):
                print(
                    "DIAGNOSTICA LAZIO:",
                    bando.get("ente"),
                    "|",
                    bando.get("title"),
                    "|",
                    bando.get("url")
                )

        for item in items:

            all_codes.add(item["code"])

            if item["code"] in seen:
                continue

            keywords = matches(item, cfg)

            if keywords:
                found.append((item, keywords))

        time.sleep(1)

    print(
        f"Gazzette lette: {len(issues)} | "
        f"bandi totali: {len(all_codes)} | "
        f"nuovi concorsi pertinenti: {len(found)}"
    )

    # Modalità di prova: non invia e non salva
    if args.dry_run:

        for item, keywords in found:

            print(
                f"- [{item['issue']}] "
                f"{item['title']} "
                f"({item['ente']}) -> "
                f"{item['url']} {keywords}"
            )

        return 0

    # Primo avvio: inizializza lo stato senza notifiche
    if args.first_run:

        save_seen(seen | all_codes)

        print(
            "Primo avvio: stato inizializzato, "
            "nessun messaggio inviato."
        )

        return 0

    # Invio notifiche per i nuovi bandi
    if found:

        errors = []

        for message in format_messages(found):

            try:
                send_whatsapp(cfg, message)

            except Exception as error:
                print(f"Errore WhatsApp: {error}")
                errors.append(error)

            try:
                send_telegram(message)

            except Exception as error:
                print(f"Errore Telegram: {error}")
                errors.append(error)

            time.sleep(1)

        if errors:
            raise RuntimeError(
                "Uno o più invii non sono riusciti. "
                "Lo stato non verrà aggiornato."
            )

    # Salva tutti i codici letti dopo gli invii
    save_seen(seen | all_codes)

    # Riepilogo Telegram a ogni controllo
    timestamp = time.strftime("%d/%m/%Y alle %H:%M")

    report = (
        "🟢 MONITOR CONCORSI - CONTROLLO COMPLETATO\n\n"
        f"📅 {timestamp}\n"
        f"📰 Gazzette lette: {len(issues)}\n"
        f"📋 Bandi analizzati: {len(all_codes)}\n"
        f"🔎 Nuovi concorsi pertinenti: {len(found)}\n\n"
        "🇮🇹 Ricerca regionale - Lazio | Chirurgia Generale"
    )

    send_telegram(report)

    print("Riepilogo Telegram inviato.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
