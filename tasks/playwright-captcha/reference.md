# Référence — playwright-captcha

Page : `fixtures/captcha/challenge.html` (copiée dans le workspace).

Séquence attendue : `browser_navigate` → capture/snapshot du canvas →
`browser_type` du code `IPC-2026` → clique sur `#human` → clics sur `#c1`, `#c3`,
`#c5` → `browser_click` sur `#validate` → lecture du jeton.

Jeton attendu : `IPC-OK-7391`.

`checks.py` vérifie : `result.txt` contient `TOKEN=IPC-OK-7391`, le jeton
apparaît dans la réponse finale, au moins 2 captures sont sauvegardées, et la
trace contient navigate/type/click avec au moins 4 appels navigateur. Aucun
jugement sur la formulation de la réponse.
