import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sqlite3
import re
import os
import time
import math
from datetime import datetime

ALARM_GRENS = 60

# Per categorie worden iedere automatische ronde
# 5 volgende pagina's gecontroleerd.
PAGINAS_PER_RONDE = 5

CATEGORIEEN = {
    "💻 Laptops": "https://www.mediamarkt.nl/nl/category/laptops-433.html",
    "📱 Smartphones": "https://www.mediamarkt.nl/nl/category/smartphones-283.html",
    "📺 Televisies": "https://www.mediamarkt.nl/nl/category/televisies-453.html",
    "🖥️ Monitoren": "https://www.mediamarkt.nl/nl/category/monitoren-667.html",
    "📟 Tablets": "https://www.mediamarkt.nl/nl/category/tablets-678.html",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PrijsFoutScanner/1.0)"
}

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def stuur_telegram(naam, oude_prijs, nieuwe_prijs, daling, link):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram niet geladen.")
        return

    bericht = (
        "🚨 MOGELIJKE PRIJSFOUT 🚨\n\n"
        "🏪 MEDIAMARKT\n"
        f"🛒 {naam}\n\n"
        f"❌ Oude prijs: €{oude_prijs:.2f}\n"
        f"🔥 Nieuwe prijs: €{nieuwe_prijs:.2f}\n"
        f"📉 Daling: {daling:.1f}%\n\n"
        f"🔗 {link}"
    )

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": bericht
            },
            timeout=15
        )

        if r.ok:
            print("      📲 Telegram-alarm verstuurd!")
        else:
            print("      ❌ Telegram-alarm mislukt.")

    except requests.RequestException as fout:
        print(f"      ❌ Telegram-fout: {fout}")


def haal_pagina(url):
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )
        r.raise_for_status()

        return BeautifulSoup(
            r.text,
            "html.parser"
        )

    except requests.RequestException as fout:
        print(f"   ❌ Ophalen mislukt: {fout}")
        return None


def vind_totaal_producten(soup):
    tekst = soup.get_text(" ", strip=True)

    matches = re.findall(
        r'(\d+)\s+van\s+(\d[\d.]*)',
        tekst,
        re.IGNORECASE
    )

    for zichtbaar, totaal in matches:
        if int(zichtbaar) == 12:
            return int(totaal.replace(".", ""))

    return None


def verwerk_producten(soup, basis_url, cursor, alle_links):

    # Voor iedere URL bewaren we de link met de beste productnaam.
    kandidaten = {}

    for a in soup.find_all("a", href=True):

        if "/nl/product/" not in a["href"]:
            continue

        link = urljoin(
            basis_url,
            a["href"]
        )

        naam = a.get_text(
            " ",
            strip=True
        )

        if (
            link not in kandidaten
            or len(naam) >
            len(kandidaten[link].get_text(" ", strip=True))
        ):
            kandidaten[link] = a

    gevonden = 0

    for link, a in kandidaten.items():

        if link in alle_links:
            continue

        blok = a
        product_blok = None

        # Zoek het kleinste blok dat alleen
        # bij dit product hoort.
        for _ in range(30):

            blok = blok.parent

            if blok is None:
                break

            tekst = blok.get_text(
                " ",
                strip=True
            )

            productlinks = {
                urljoin(basis_url, x["href"])
                for x in blok.find_all("a", href=True)
                if "/nl/product/" in x["href"]
            }

            if (
                "incl. BTW" in tekst
                and productlinks == {link}
            ):
                product_blok = blok
                break

        if product_blok is None:
            continue

        tekst = product_blok.get_text(
            " ",
            strip=True
        )

        # BELANGRIJK:
        # alleen de prijs direct vóór "incl. BTW".
        # Zo pakken we niet per ongeluk een oude,
        # advies- of refurbished-prijs.
        match = re.search(
            r'€\s*(\d+(?:\.\d{3})*)'
            r'\s*,\s*(\d{2})'
            r'\s+incl\.\s*BTW',
            tekst
        )

        if not match:
            continue

        euro = match.group(1).replace(".", "")
        cent = match.group(2)

        prijs = (
            float(euro)
            + int(cent) / 100
        )

        naam = a.get_text(
            " ",
            strip=True
        )

        if not naam:
            naam = "MediaMarkt product"

        alle_links.add(link)

        nu = datetime.now().isoformat(
            timespec="seconds"
        )

        cursor.execute(
            "SELECT last_price FROM products WHERE url = ?",
            (link,)
        )

        bestaand = cursor.fetchone()

        if bestaand is None:

            print(
                f"      🆕 {naam[:55]} "
                f"→ €{prijs:.2f}"
            )

        else:

            oude_prijs = bestaand[0]

            if (
                prijs < oude_prijs
                and oude_prijs > 0
            ):

                daling = (
                    (oude_prijs - prijs)
                    / oude_prijs
                ) * 100

                if daling >= ALARM_GRENS:

                    print(
                        f"      🚨 {naam[:55]}: "
                        f"€{oude_prijs:.2f} "
                        f"→ €{prijs:.2f} "
                        f"({daling:.1f}%)"
                    )

                    stuur_telegram(
                        naam,
                        oude_prijs,
                        prijs,
                        daling,
                        link
                    )

        cursor.execute("""
            INSERT INTO products
            (url, name, last_price, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                name = excluded.name,
                last_price = excluded.last_price,
                last_seen = excluded.last_seen
        """, (
            link,
            naam,
            prijs,
            nu
        ))

        cursor.execute("""
            INSERT INTO price_history
            (url, price, checked_at)
            VALUES (?, ?, ?)
        """, (
            link,
            prijs,
            nu
        ))

        gevonden += 1

    return gevonden


# =========================================
# DATABASE
# =========================================

db = sqlite3.connect("prijsfout.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    url TEXT PRIMARY KEY,
    name TEXT,
    last_price REAL,
    last_seen TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT,
    price REAL,
    checked_at TEXT
)
""")

# Hier onthouden we waar iedere categorie gebleven is.
cursor.execute("""
CREATE TABLE IF NOT EXISTS mediamarkt_scan_state (
    category TEXT PRIMARY KEY,
    next_page INTEGER NOT NULL
)
""")

db.commit()


# =========================================
# SCANNER
# =========================================

print()
print("🔎 MediaMarkt ALLE-PAGINA'S scanner gestart...")
print(
    f"🔄 {PAGINAS_PER_RONDE} pagina's "
    "per categorie per ronde"
)
print()

ronde_totaal = 0
alle_links = set()

for categorie, basis_url in CATEGORIEEN.items():

    print("=" * 60)
    print(categorie)

    # Pagina 1 gebruiken om te ontdekken
    # hoeveel producten er NU zijn.
    eerste_soup = haal_pagina(basis_url)

    if eerste_soup is None:
        continue

    totaal_producten = vind_totaal_producten(
        eerste_soup
    )

    if not totaal_producten:
        print(
            "   ❌ Totaal aantal producten "
            "kon niet worden gevonden."
        )
        continue

    totaal_paginas = math.ceil(
        totaal_producten / 12
    )

    print(
        f"   📦 {totaal_producten} producten"
    )
    print(
        f"   📚 {totaal_paginas} pagina's totaal"
    )

    cursor.execute("""
        SELECT next_page
        FROM mediamarkt_scan_state
        WHERE category = ?
    """, (categorie,))

    state = cursor.fetchone()

    if state is None:
        start_pagina = 1
    else:
        start_pagina = state[0]

    if start_pagina > totaal_paginas:
        start_pagina = 1

    paginas = []

    for stap in range(PAGINAS_PER_RONDE):

        pagina = (
            (start_pagina - 1 + stap)
            % totaal_paginas
        ) + 1

        paginas.append(pagina)

    print(
        "   🔍 Deze ronde pagina's:",
        ", ".join(str(p) for p in paginas)
    )

    categorie_totaal = 0

    for pagina in paginas:

        print(
            f"   🌐 Pagina {pagina}/{totaal_paginas}"
        )

        if pagina == 1:
            soup = eerste_soup
        else:
            pagina_url = (
                f"{basis_url}?page={pagina}"
            )

            soup = haal_pagina(pagina_url)

        if soup is None:
            continue

        aantal = verwerk_producten(
            soup,
            basis_url,
            cursor,
            alle_links
        )

        categorie_totaal += aantal
        ronde_totaal += aantal

        db.commit()

        # Rustig richting MediaMarkt.
        time.sleep(2)

    volgende_pagina = (
        (paginas[-1])
        % totaal_paginas
    ) + 1

    cursor.execute("""
        INSERT INTO mediamarkt_scan_state
        (category, next_page)
        VALUES (?, ?)
        ON CONFLICT(category) DO UPDATE SET
            next_page = excluded.next_page
    """, (
        categorie,
        volgende_pagina
    ))

    db.commit()

    print(
        f"   ✅ Deze ronde: "
        f"{categorie_totaal} producten"
    )
    print(
        f"   ⏭️ Volgende ronde begint "
        f"bij pagina {volgende_pagina}"
    )
    print()

db.close()

print("=" * 60)
print(
    f"🎯 MEDIAMARKT DEZE RONDE: "
    f"{ronde_totaal} producten"
)
print(
    "🔄 Volgende automatische scan gaat "
    "verder met de volgende pagina's."
)
print(
    f"🚨 Alarm vanaf {ALARM_GRENS}% prijsdaling."
)
print("=" * 60)
