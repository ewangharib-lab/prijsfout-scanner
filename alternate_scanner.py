import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sqlite3
import re
import os
from datetime import datetime

URL = "https://www.alternate.nl/Laptop/Alle-laptops"
ALARM_GRENS = 60

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
        "🏪 ALTERNATE\n"
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
            print("📲 Telegram-alarm verstuurd!")
        else:
            print("❌ Telegram-alarm mislukt.")

    except requests.RequestException as fout:
        print(f"❌ Telegram-fout: {fout}")


print("🔎 ALTERNATE Scanner gestart...")

try:
    r = requests.get(URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
except requests.RequestException as fout:
    print(f"❌ ALTERNATE kon niet worden opgehaald: {fout}")
    raise SystemExit

soup = BeautifulSoup(r.text, "html.parser")

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

gezien = set()
totaal = 0

for a in soup.find_all("a", href=True):

    href = a["href"]

    if "/html/product/" not in href:
        continue

    link = urljoin(URL, href)

    if link in gezien:
        continue

    tekst = a.get_text(" ", strip=True)

    prijzen = re.findall(
        r'€\s*(\d{1,3}(?:\.\d{3})*),(\d{2})',
        tekst
    )

    if not prijzen:
        continue

    # Bij een oude + nieuwe aanbiedingsprijs
    # nemen we de laatste prijs.
    euro, cent = prijzen[-1]

    prijs = (
        float(euro.replace(".", ""))
        + int(cent) / 100
    )

    naam = tekst.split("Processor:")[0].strip()

    if not naam:
        naam = "ALTERNATE product"

    gezien.add(link)

    nu = datetime.now().isoformat(timespec="seconds")

    cursor.execute(
        "SELECT last_price FROM products WHERE url = ?",
        (link,)
    )

    bestaand = cursor.fetchone()

    if bestaand is None:
        print(f"🆕 {naam[:70]} → €{prijs:.2f}")

    else:
        oude_prijs = bestaand[0]

        if prijs < oude_prijs and oude_prijs > 0:

            daling = (
                (oude_prijs - prijs)
                / oude_prijs
            ) * 100

            if daling >= ALARM_GRENS:

                print(
                    f"🚨 {naam[:70]}: "
                    f"€{oude_prijs:.2f} → €{prijs:.2f} "
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
    """, (link, naam, prijs, nu))

    cursor.execute("""
        INSERT INTO price_history
        (url, price, checked_at)
        VALUES (?, ?, ?)
    """, (link, prijs, nu))

    totaal += 1

db.commit()
db.close()

print()
print(f"✅ ALTERNATE: {totaal} producten gecontroleerd.")
print(f"🚨 Alarm bij minimaal {ALARM_GRENS}% prijsdaling.")
print("💾 Prijzen opgeslagen in dezelfde database.")
