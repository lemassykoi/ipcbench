# Référence — long-context-log-dig

Ligne cible (index 1873 sur 3000) :

```
2026-11-19T04:02:17Z fail2ban.actions: BAN 198.51.100.73 jail=postfix-sasl geo=FR
reason=6_failed_AUTH_on_port_587 session=00001873-FR-postfix-sasl-confirmed
```

Réponse attendue :

```
IP=198.51.100.73
PAYS=FR
```

Le corpus contient volontairement de nombreuses lignes `BAN` avec des IP et
des horaires proches, mais une seule combinaison date/heure/jail/pays autorisé
est correcte. La note vérifie les deux valeurs exactes, pas la prose.
