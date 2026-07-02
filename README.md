# unsere-eeg.at FAQ

Community-FAQ für **EEG Faktura** (Online-Portal) und das **EEG Faktura Tool** (Desktop) —
live unter **https://faq.unsere-eeg.at**

Die Einträge wurden per KI aus den WhatsApp-Community-Gruppen zusammengefasst und werden
wöchentlich automatisch mit lokaler KI (on-premise) um neue Fragen ergänzt.

## Mitmachen 🙌

Fehler gefunden? Bessere Antwort? **Pull Request auf `faq_data.json`** — jede Verbesserung hilft der Community!

### Struktur von `faq_data.json`

```json
{
  "portal": { "faq": [...], "spezial": [...] },   // EEG Faktura (Online-Portal)
  "tool":   { "faq": [...], "spezial": [...] }    // EEG Faktura Tool (Desktop, stromregion.at)
}
```

Jeder Eintrag:
```json
{
  "frage":     "Kurze, klare Frage?",
  "antwort":   "<p>Antwort als HTML — erlaubt: p, ol, ul, li, strong, code</p>",
  "kategorie": "Einrichtung | Mitglieder | Zählpunkte/Netzbetreiber | Abrechnung | Tarife | Steuer/USt | Fehler/Störung | Bedienung | Installation/Updates | Sonstiges",
  "count":     3   // wie oft die Frage in der Community aufgetaucht ist
}
```

- `faq` = häufige Fragen · `spezial` = seltene, aber lehrreiche Spezialfälle
- **Keine personenbezogenen Daten** (Namen, Nummern, Zählpunkte) — PRs damit werden abgelehnt
- Merges gehen beim nächsten wöchentlichen Build automatisch live

## Technik

- `faq_build2.py` — generiert die statische Seite aus `faq_data.json`
- `faq_update.py` — wöchentlicher Auto-Update-Job (läuft on-premise; zieht neue Community-Fragen,
  dedupliziert per Embeddings, committet hierher zurück)

*Kein offizieller Support des EEG-Faktura-Betreibers. Ohne Gewähr.*
