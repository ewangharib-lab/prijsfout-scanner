import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sqlite3
import re
import os
import time
from datetime import datetime

ALARM_GRENS = 60
PAGINAS_PER_CATEGORIE = 5

CATEGORIEEN = {
    "📱 Smartphones": "https://www.coolblue.nl/mobiele-telefoons/smartphones",
    "💻 Laptops": "https://www.coolblue.nl/laptops/filter",
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


# -----------------------------
# DATABASE
# -----------------------------

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

db.commit()


# -----------------------------
# SCANNEN
# -----------------------------

print("🔎 PrijsFout Scanner gestart...")
print(f"🏪 Categorieën: {len(CATEGORIEEN)}")
print(f"📄 Pagina's per categorie: {PAGINAS_PER_CATEGORIE}")
print()

totaal = 0
unieke_urls = set()

for categorie, basis_url in CATEGORIEEN.items():

    print("=" * 60)
    print(categorie)
    print("=" * 60)

    categorie_totaal = 0

    for pagina in range(1, PAGINAS_PER_CATEGORIE + 1):

        if pagina == 1:
            url = basis_url
        else:
            url = f"{basis_url}?pagina={pagina}"

        print(f"🌐 Pagina {pagina} ophalen...")

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=15
            )
            response.raise_for_status()

        except requests.RequestException as fout:
            print(f"   ❌ Mislukt: {fout}")
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select(".product-card")

        print(f"   📦 {len(cards)} productkaarten")

        for card in cards:

            links = [
                a for a in card.find_all("a", href=True)
                if "/product/" in a.get("href", "")
            ]

            if not links:
                continue

            product_link = max(
                links,
                key=lambda a: len(a.get_text(" ", strip=True))
            )

            naam = product_link.get_text(" ", strip=True)
            link = urljoin(basis_url, product_link["href"])

            if link in unieke_urls:
                continue

            tekst = card.get_text(" ", strip=True)

            match = re.search(
                r'(\d{1,3}(?:\.\d{3})*)\s*,\s*(-|\d{2})\s+Op voorraad',
                tekst
            )

            if not match:
                continue

            euros = match.group(1).replace(".", "")
            centen = match.group(2)

            prijs = float(euros)

            if centen != "-":
                prijs += int(centen) / 100

            unieke_urls.add(link)

            nu = datetime.now().isoformat(timespec="seconds")

            cursor.execute(
                "SELECT last_price FROM products WHERE url = ?",
                (link,)
            )

            bestaand = cursor.fetchone()

            if bestaand is None:
                print(f"   🆕 {naam} → €{prijs:.2f}")

            else:
                oude_prijs = bestaand[0]

                if prijs < oude_prijs and oude_prijs > 0:
                    daling = (
                        (oude_prijs - prijs)
                        / oude_prijs
                    ) * 100

                    if daling >= ALARM_GRENS:

                        print("   🚨 MOGELIJKE PRIJSFOUT")
                        print(
                            f"      €{oude_prijs:.2f}"
                            f" → €{prijs:.2f}"
                            f" ({daling:.1f}%)"
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
            """, (link, naam, prijs, nu))

            cursor.execute("""
                INSERT INTO price_history
                (url, price, checked_at)
                VALUES (?, ?, ?)
            """, (link, prijs, nu))

            categorie_totaal += 1
            totaal += 1

        db.commit()

        if pagina < PAGINAS_PER_CATEGORIE:
            time.sleep(2)

    print(f"✅ {categorie}: {categorie_totaal} unieke producten")
    print()

    # Kleine pauze tussen categorieën
    time.sleep(3)

db.close()

print("=" * 60)
print(f"🎯 TOTAAL: {totaal} unieke producten gecontroleerd")
print(f"🚨 Telegram-alarm vanaf {ALARM_GRENS}% prijsdaling")
print("💾 Prijshistorie opgeslagen")
print("=" * 60)
