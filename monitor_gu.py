
#!/usr/bin/env python3
"""Monitor Gazzetta Ufficiale - Chirurgia Generale nel Lazio."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.gazzettaufficiale.it"
LIST_URL = f"{BASE}/30giorni/concorsi"
HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.toml"

# Stato separato dal vecchio monitor nazionale.
STATE_PATH = HERE / "seen_lazio.json"

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

    for link in soup.find_all("a", href=True):
        href = link["href"]

        if (
            "caricaDettaglio" in href
            and "dataPubblicazioneGazzetta" in href
        ):
            url = urljoin(BASE, href)

            if url not in seen_urls:
                seen_urls.add(url)
                label = " ".join(
                    link.get_text(" ", strip=True).split()
                )
                issues.append((label, url))

    return issues


def parse_issue(label: str, url: str) -> list[dict]:
    """Estrae codice, titolo, ente e collegamento di ciascun atto."""

    soup = get(url)
    items = {}
    ente = ""

    for element in soup.find_all(
        ["span", "div", "h2", "h3", "h4", "strong", "a"]
    ):
        classes = " ".join(element.get("class", []))

        if (
            element.name != "a"
            and re.search(r"emettitore|ente|rubrica", classes, re.I)
        ):
            text = " ".join(
                element.get_text(" ", strip=True).split()
            )

            if text:
                ente = text

            continue

        if element.name == "a" and element.get("href"):
            match = CODICE_RE.search(element["href"])

            if not match:
                continue

            code = match.group(1)
            title = " ".join(
                element.get_text(" ", strip=True).split()
            )

            if not title or code in items:
                continue

            items[code] = {
                "code": code,
                "title": title,
                "ente": ente,
                "url": urljoin(BASE, element["href"]),
                "issue": label,
            }

    return list(items.values())


# ---------------------------------------------------------------------------
# TESTO INTEGRALE DEI BANDI
# ---------------------------------------------------------------------------

_TEXT_CACHE: dict[str, str] = {}


def fetch_text(url: str) -> str:
    """Scarica il testo completo del bando, con cache."""

    if url not in _TEXT_CACHE:
        try:
            _TEXT_CACHE[url] = " ".join(
                get(url).get_text(" ", strip=True).split()
            )

        except RuntimeError as error:
            print(
                f"ATTENZIONE: testo non recuperabile ({url}): {error}",
                file=sys.stderr
            )
            _TEXT_CACHE[url] = ""

        time.sleep(0.5)

    return _TEXT_CACHE[url]


def normalize(value: str) -> str:
    """Minuscolo, senza accenti e con spazi uniformi."""

    value = unicodedata.normalize(
        "NFKD", value
    ).encode("ascii", "ignore").decode("ascii")

    value = value.lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return " ".join(value.split())


# ---------------------------------------------------------------------------
# ENTI SANITARI DEL LAZIO
# ---------------------------------------------------------------------------

ASL_LAZIO_PATTERNS = [
    re.compile(
        r"\b(?:asl|azienda sanitaria locale)\s+"
        r"(?:di\s+)?(?:roma|rm)\s*[1-6]\b"
    ),
    re.compile(
        r"\b(?:asl|azienda sanitaria locale)\s+"
        r"(?:di\s+)?(?:frosinone|latina|rieti|viterbo)\b"
    ),
]


ENTI_LAZIO = [
    "azienda ospedaliera san camillo forlanini",
    "san camillo forlanini",
    "azienda ospedaliera san giovanni addolorata",
    "san giovanni addolorata",
    "policlinico umberto i",
    "policlinico umberto primo",
    "policlinico tor vergata",
    "azienda ospedaliero universitaria sant andrea",
    "azienda ospedaliera sant andrea",
    "ospedale sant andrea",
    "istituto nazionale per le malattie infettive lazzaro spallanzani",
    "inmi lazzaro spallanzani",
    "lazzaro spallanzani",
    "istituti fisioterapici ospitalieri",
    "istituto nazionale tumori regina elena",
    "regina elena",
    "fondazione policlinico universitario agostino gemelli",
    "policlinico gemelli",
    "fondazione policlinico universitario campus bio medico",
    "policlinico campus bio medico",
    "ospedale pediatrico bambino gesu",
    "bambino gesu",
    "ospedale isola tiberina",
    "gemelli isola",
    "fatebenefratelli",
    "ospedale israelitico",
    "istituto dermopatico dell immacolata",
    "istituto dermopatico immacolata",
    "ares 118",
    "regione lazio",
]


LOCALITIES_LAZIO = [
    "roma", "frosinone", "latina", "rieti", "viterbo",
    "civitavecchia", "tivoli", "guidonia", "pomezia",
    "anzio", "aprilia", "cassino", "sora", "formia",
    "terracina", "velletri", "colleferro", "monterotondo",
    "subiaco", "tarquinia", "cerveteri", "bracciano",
    "nettuno", "frascati", "marino", "albano laziale",
    "fondi", "gaeta", "minturno", "isola del liri",
    "ceccano", "alatri", "ferentino", "anagni",
    "palestrina", "ladispoli", "fiumicino", "capena",
    "ronciglione", "montefiascone", "cittaducale",
    "poggio mirteto",
]


WORKPLACE_CONTEXT = re.compile(
    r"\b(?:sede|sedi|luogo di lavoro|sede di servizio|"
    r"sede di lavoro|assegnazione|destinazione|presso|"
    r"posto|posti|struttura sanitaria|struttura|presidio|"
    r"ospedale|distretto|reparto|unita operativa|"
    r"ambulatorio|servizio|articolazione territoriale)\b"
)


def is_lazio_entity(text: str) -> bool:
    """Verifica che l'ente sia uno degli enti sanitari laziali."""

    normalized = normalize(text)

    if any(
        pattern.search(normalized)
        for pattern in ASL_LAZIO_PATTERNS
    ):
        return True

    return any(
        name in normalized
        for name in ENTI_LAZIO
    )


def has_lazio_workplace(text: str) -> bool:
    """Cerca una sede laziale vicina a indicazioni di lavoro o servizio."""

    locality_pattern = re.compile(
        r"\b(?:"
        + "|".join(
            re.escape(x)
            for x in sorted(
                LOCALITIES_LAZIO,
                key=len,
                reverse=True
            )
        )
        + r")\b"
    )

    # Separiamo le frasi prima di normalizzare.
    # Evita di associare una sede in un'altra regione
    # a una citazione casuale di Roma in una frase successiva.
    for raw_sentence in re.split(
        r"[.;:!?]+|[\n\r]+",
        text
    ):
        sentence = normalize(raw_sentence)

        if (
            locality_pattern.search(sentence)
            and WORKPLACE_CONTEXT.search(sentence)
        ):
            return True

    return False


# ---------------------------------------------------------------------------
# FILTRO DELLE PROCEDURE
# ---------------------------------------------------------------------------

FOLLOW_UP_ONLY = re.compile(
    r"^\s*(?:diario|annullamento|revoca|graduatoria|esito|"
    r"convocazione|calendario|elenco ammessi|ammissione)\b",
    re.IGNORECASE,
)



def matches(item: dict, cfg: dict) -> list[str]:
    """
    Seleziona opportunita' di Chirurgia Generale con ente o sede nel Lazio.
    Scarica il testo integrale solo quando necessario.
    """

    filters = cfg.get("filtri", {})

    title = str(item.get("title", ""))
    entity = str(item.get("ente", ""))

    title_entity = normalize(f"{title} {entity}")

    # Esclude comunicazioni che non rappresentano nuove opportunita'.
    if FOLLOW_UP_ONLY.search(normalize(title)):
        return []

    exclusions = [
        normalize(str(x))
        for x in filters.get("escludi", [])
    ]

    if any(word and word in title_entity for word in exclusions):
        return []

    # Primo controllo: ente laziale.
    local_employer = (
        is_lazio_entity(entity)
        or is_lazio_entity(title)
    )

    raw_full_text = f"{title} {entity}"

    # Se l'ente non e' laziale, cerca prima la sede nel Lazio
    # usando titolo ed ente. Solo se non basta scarica il testo.
    if not local_employer:
        if not has_lazio_workplace(raw_full_text):
            if not filters.get("cerca_nel_testo", True):
                return []

            raw_full_text += " " + fetch_text(item["url"])

            if not has_lazio_workplace(raw_full_text):
                return []

    # Per gli enti laziali scarica il testo per verificare la specialita'.
    elif filters.get("cerca_nel_testo", True):
        raw_full_text += " " + fetch_text(item["url"])

    full_text = normalize(raw_full_text)

    include = [
        normalize(str(x))
        for x in filters.get("parole_chiave", [])
    ]

    include = [word for word in include if word]

    if not include:
        include = ["chirurgia generale", "chirurgo generale"]

    found = [
        keyword
        for keyword in include
        if keyword in full_text
    ]

    if not found:
        return []

    if normalize(title).startswith("rettifica"):
        found.append("AGGIORNAMENTO/RETTIFICA")

    return found


# ---------------------------------------------------------------------------
# WHATSAPP
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
# TELEGRAM
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

    for item, keywords in found:

        is_rectification = (
            "AGGIORNAMENTO/RETTIFICA" in keywords
        )

        specialty = [
            x for x in keywords
            if x != "AGGIORNAMENTO/RETTIFICA"
        ]

        tag_text = (
            "\n⚠️ RETTIFICA/AGGIORNAMENTO"
            if is_rectification
            else ""
        )

        keyword_text = (
            f"\n🔎 {', '.join(specialty)}"
            if specialty
            else ""
        )

        blocks.append(
            f"📌 {item['title']}{tag_text}\n"
            f"🏛 {item['ente'] or 'n/d'}\n"
            f"📰 {item['issue']}{keyword_text}\n"
            f"🔗 {item['url']}"
        )

    messages = []

    current = (
        f"📢 Nuove opportunita' - "
        f"Chirurgia Generale nel Lazio ({len(found)})\n"
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

    print("\n--- DIAGNOSTICA FILTRO LAZIO ---")
    print("Enti sanitari riconosciuti:")

    for ente in ENTI_LAZIO:
        print(f"  - {ente}")

    print("--- FINE DIAGNOSTICA ---\n")

    parser = argparse.ArgumentParser()


    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--first-run", action="store_true")
    parser.add_argument("--test-whatsapp", action="store_true")
    parser.add_argument("--test-telegram", action="store_true")

    args = parser.parse_args()

    cfg = load_config()

    if args.test_whatsapp:

        send_whatsapp(
            cfg,
            "✅ gu-monitor: messaggio di prova WhatsApp."
        )

        print("Messaggio WhatsApp inviato.")
        return 0

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
            "ATTENZIONE: nessuna Gazzetta trovata.",
            file=sys.stderr
        )
        return 2

    found = []
    count_local_entity = 0
    count_local_workplace = 0
    count_surgery = 0
    all_codes = set()

    for label, url in issues:

        items = parse_issue(label, url)

        for item in items:

            code = item["code"]
            all_codes.add(code)

            # In dry-run si rivalutano anche i bandi gia' visti.
            # Non viene modificato alcuno stato.
            if not args.dry_run and code in seen:
                continue

            if is_lazio_entity(item.get("ente", "")) or is_lazio_entity(item.get("title", "")):
                count_local_entity += 1

            text_to_check = normalize(
                item.get("title", "") + " " + item.get("ente", "")
            )

            if has_lazio_workplace(
                item.get("title", "") + " " + item.get("ente", "")
            ):
                count_local_workplace += 1

            if any(
                normalize(k) in text_to_check
                for k in cfg.get("filtri", {}).get("parole_chiave", [])
            ):
                count_surgery += 1

            keywords = matches(item, cfg)
            if keywords:
                found.append((item, keywords))

        time.sleep(0.5)

    
    print("\n--- RISULTATI DIAGNOSTICA ---")
    print(f"Enti riconosciuti nel Lazio: {count_local_entity}")
    print(f"Bandi con sede di lavoro nel Lazio: {count_local_workplace}")
    print(f"Bandi con riferimento a Chirurgia Generale nel titolo/ente: {count_surgery}")
    print("--- FINE DIAGNOSTICA ---\n")

    print(
        f"Gazzette lette: {len(issues)} | "
        f"bandi analizzati: {len(all_codes)} | "
        f"opportunita' pertinenti: {len(found)}"
    )


    if args.dry_run:

        for item, keywords in found:
            print(
                f"- [{item['issue']}] "
                f"{item['title']} "
                f"({item['ente']}) -> "
                f"{item['url']} {keywords}"
            )

        print(
            "DRY-RUN: nessuna notifica inviata "
            "e nessuno stato modificato."
        )

        return 0

    if args.first_run:

        save_seen(seen | all_codes)

        print(
            "Primo avvio: stato Lazio inizializzato, "
            "nessun messaggio inviato."
        )

        return 0

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
                "Uno o piu' invii non sono riusciti. "
                "Lo stato non verra' aggiornato."
            )

    # Salva i codici dopo gli invii.
    # Il precedente seen.json resta intatto.
    save_seen(seen | all_codes)

    timestamp = time.strftime("%d/%m/%Y alle %H:%M")

    report = (
        "🟢 MONITOR CONCORSI - CONTROLLO COMPLETATO\n\n"
        f"📅 {timestamp}\n"
        f"📰 Gazzette lette: {len(issues)}\n"
        f"📋 Bandi analizzati: {len(all_codes)}\n"
        f"🔎 Nuove opportunita' pertinenti: {len(found)}\n\n"
        "🇮🇹 Lazio - Chirurgia Generale"
    )

    send_telegram(report)

    print("Riepilogo Telegram inviato.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
