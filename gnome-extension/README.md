# AI Usage Indicator (`aiu-indicator`)

Extension GNOME Shell (compatible GNOME 45 à 51+, testée sur GNOME Shell 50.1 Wayland) plaçant une icône discrète à côté de la date et de l'heure dans la barre supérieure, présentant l'ensemble des données et quotas agrégés par `aiu`.

## Fonctionnalités

- **Emplacement central** : Intégration dans le bloc central du panneau (`_centerBox`, index 1) immédiatement à droite de l'horloge.
- **Exécution 100% asynchrone & non-bloquante** : Interrogation en arrière-plan via `Gio.Subprocess` sans aucun freeze ni blocage de l'interface GNOME.
- **Statut visuel d'un coup d'œil** :
  - Pastille verte : tous les quotas sont sous contrôle.
  - Pastille jaune : au moins un quota approche de la saturation (≥ 75%).
  - Pastille rouge : quota saturé (≥ 95%) ou alerte d'authentification / jeton expiré (ex: Claude 401).
- **Menu complet et soigné** :
  - **Forfaits & Quotas** : Barres de progression dynamiques avec code couleur, pourcentages et compte à rebours de reset (ex: `in 49m`).
  - **Soldes & Consommation** : DeepSeek, OpenRouter, soldes en USD et métriques de dépenses.
  - **Conseil d'arbitrage (`--advise`)** : Recommandation automatique du forfait ayant le plus de marge disponible.
  - **Alertes explicites** : Affichage sans omission des erreurs ou jetons expirés avec instruction de rafraîchissement.
  - **Bouton d'actualisation instantanée** et actualisation automatique toutes les 5 minutes (ou à l'ouverture du menu si > 2 min).
  - **Raccourcis rapides** : Ouverture de `aiu` dans le terminal et liens directs vers les consoles web IA (AI Studio, Anthropic, ChatGPT, OpenRouter, DeepSeek).

## Installation

```bash
git clone https://github.com/citizenu03bb/aiu-indicator.git ~/Projects/aiu-indicator
cd ~/Projects/aiu-indicator
make install
```

## Commandes Make

- `make check` : Vérifie la syntaxe de tous les fichiers JavaScript et de `metadata.json`.
- `make test` : Exécute les tests unitaires asynchrones avec GJS.
- `make install` : Crée le lien symbolique dans `~/.local/share/gnome-shell/extensions/` et active l'extension.
- `make reload` : Désactive puis réactive l'extension pour prise en compte à chaud.
- `make uninstall` : Désactive et retire le lien symbolique.
- `make status` : Affiche l'état de l'extension via `gnome-extensions info`.
- `make log` : Filtre les logs de GNOME Shell pour l'extension.

## Invariants & Architecture

- **Zéro fallback destructeur** : Si `aiu` échoue ou renvoie une erreur, l'état d'erreur est affiché textuellement, jamais masqué par de fausses données.
- **Hygiène système** : `disable()` désenregistre scrupuleusement tous les timeouts `GLib.Source`, annule les `Gio.Cancellable` en cours et détruit les conteneurs d'acteurs Clutter.
