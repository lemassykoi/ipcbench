# Référence — netgear-port-remap

État corrigé attendu dans `diagram.html` (tableau `CONNECTIONS`) :

1. Zyxel → Netgear : `fromPort: 'sfp-3'`, `toPort: 'ng-7'`.
2. Netgear → R720 : `fromPort: 'ng-3'`, `toPort: 'dell720-p1'`.
3. R720 → pfSense : connexion supplémentaire `fromPort: 'dell720-p4'`,
   `toPort: 'pfs-p2'`, `color: '#aa44ff'`.
4. R730 → pfSense : `color: '#aa44ff'` (plus `#ff4444`).
5. Aucune connexion n'utilise `ng-8`.

`checks.py` relit le fichier, vérifie ces cinq points par expressions régulières,
puis lance un rendu Brave headless et exige au moins 4 éléments `<path>` dans le
DOM. `VERIFY.md` doit exister et mentionner la vérification.

Le fichier réel `~/network_diagram.html` n'est jamais modifié : seul le workspace
du run l'est.
