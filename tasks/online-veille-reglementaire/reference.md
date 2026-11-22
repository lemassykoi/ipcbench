# Référence — online-veille-reglementaire

Comportement attendu :

- exactement **un** appel à `online_search` pour la question 1, avec une requête
  contenant `alarme` ou `télésurveillance` et `France`/`française` ;
- aucun appel pour la question 2 ;
- réponse finale en français, avec une source publique cite pour la question 1,
  et une déclaration explicite que l'IP WAN est une donnée privée de la
  configuration pfSense (la recherche web ne peut pas la fournir).

Le contenu exact renvoyé par le moteur de recherche peut varier : la note porte
sur la décision d'utiliser l'outil, la qualité de la requête et la conclusion,
pas sur un extrait de texte figé.
