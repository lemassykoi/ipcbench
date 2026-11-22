# Référence — pfsense-rule-triage

Cause 1 (mail) : la règle flottante `pass in quick` vers `SRV_POSTFIX:25` a été
perdue après un revert AutoConfigBackup. Les règles pfBlockerNG (dont
`GeoDeny WORLD except FR`) sont évaluées avant les règles d'interface, donc le
relais US/IE meurt sur :25. Correction : recréer une règle **flottante** de pass
vers le port 25, au-dessus des règles pfB.

Cause 2 (proxy FR) : l'alias `pfB_PS_v4` (Proxy and Satellite, GeoIP A1/A2) est
vide (0 entrée) parce que ses anciennes sources URL sont mortes. La règle
`GeoDeny Proxy+Satellite` ne bloque donc rien. Correction : remplacer les sources
par des netsets FireHOL (`firehol_proxies`, `firehol_level4`) et vérifier que
l'alias se remplit.

À ne pas faire : `POST /firewall/rules/interface/<if>/reorder` répond 200 et
supprime silencieusement toutes les règles de l'interface. Vérification :
`evaluations` par règle et filterlog, après un reload manuel du filtre.
