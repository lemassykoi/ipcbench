# Référence — blender-ipc-sensor

Contrat vérifié mécaniquement par `checks.py` (aucun jugement LLM) :

- `exports/sensor.glb` : magic `glTF`, version 2, JSON chunk parseable.
- Nœuds GLB : `SensorBody`, `MountBracket`, `StatusLED`.
- Matériau de `SensorBody` : `metallicFactor` ~0,2, `roughnessFactor` ~0,4.
- Matériau de `StatusLED` : `emissiveFactor` avec au moins une composante > 0.
- `session.blend` présent et > 10 Ko.
- Au moins 5 appels à des outils Blender dans la trace, et au moins une capture.
- `FINAL.md` mentionne le `.glb` et le `.blend`.

Le script doit être conduit dans la session live (les outils Blender sont
nécessaires) et auto-vérifié. Aucun fichier hors workspace n'est modifié.
