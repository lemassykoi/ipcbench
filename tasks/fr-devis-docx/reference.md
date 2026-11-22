# Référence — fr-devis-docx

Calculs attendus :

- centrale : 1 × 480,00 = 480,00 € HT
- détecteurs : 2 × 145,00 = 290,00 € HT
- installation : 1 × 350,00 = 350,00 € HT
- **total HT = 1 120,00 €**
- **TVA 20 % = 224,00 €**
- **total TTC = 1 344,00 €**

Le `.docx` est un OOXML minimal produit avec `docx_helper.py` ; `checks.py` ouvre
le ZIP avec `zipfile`, lit `word/document.xml` avec `xml.etree` et vérifie la
présence du numéro de devis, du client, des trois lignes, des trois montants et
de la mention de validité. `devis.txt` doit contenir les mêmes montants.

Formats numériques acceptés : `1 120,00`, `1120.00`, `1.120,00` (la note
normalise espaces, NBSP et points).
