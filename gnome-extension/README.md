# AI Usage Indicator (`aiu-indicator`)

Extension GNOME Shell, livrée avec [`aiu`](../README.md) dont elle affiche la sortie `aiu --json`.
Extension GNOME Shell (compatible GNOME 45 à 51+, testée sur GNOME Shell 50.1 Wayland) plaçant une icône discrète à côté de la date et de l'heure dans la barre supérieure, présentant l'ensemble des données et quotas agrégés par `aiu`.

## Fonctionnalités

- **Emplacement central** : Intégration dans le bloc central du panneau (`_centerBox`, index 1) immédiatement à droite de l'horloge.
- **Exécution 100% asynchrone & non-bloquante** : Interrogation en arrière-plan via `Gio.Subprocess` sans aucun freeze ni blocage de l'interface GNOME.
- **Statut visuel d'un coup d'œil** :
  - Pastille verte : rien à faire.
  - Pastille jaune : du quota gratuit sera perdu à un reset dans les 24 h (à utiliser).
  - Pastille rouge : une fenêtre est saturée (≥ 95 %) ou un relevé a échoué (ex. jeton Claude expiré).
- **Menu complet et soigné** :
  - **Forfaits** : dans l'ordre de routage, avec un badge (à brûler / au rythme / à ménager), des barres à l'échelle colorées selon ce qu'il faut en faire, et le compte à rebours du reset (ex. `Reset dans 4h27 · à sec dans ~11h33`).
  - **Crédits & API payantes** : crédits offerts avec leur expiration, soldes prépayés, et dépense historique (grisée).
  - **Conseil d'arbitrage** : reprend le champ `advice` de `aiu --json` — brûle d'abord le forfait (ou crédit) dont le surplus expire le plus tôt, et signale ceux à ménager parce qu'ils seront à sec avant leur reset.
  - **Alertes explicites** : Affichage sans omission des erreurs ou jetons expirés avec instruction de rafraîchissement.
  - **Bouton d'actualisation instantanée** et actualisation automatique toutes les 5 minutes (ou à l'ouverture du menu si > 2 min).
  - **Raccourcis rapides** : Ouverture de `aiu` dans le terminal et liens directs vers les consoles web IA (AI Studio, Anthropic, ChatGPT, OpenRouter, DeepSeek).

## Installation

Prérequis : la commande `aiu` dans `~/.local/bin` ou le `PATH` (voir le [README principal](../README.md#install)).

```bash
cd gnome-extension   # depuis votre clone du dépôt aiu
make install         # puis déconnexion / reconnexion (Wayland)
```

Sans clone, depuis le zip joint à chaque [release GitHub](https://github.com/citizenu03bb/aiu/releases) (ou construit avec `make pack`, au format attendu par extensions.gnome.org) :

```bash
gnome-extensions install --force aiu-indicator@citizenu03bb.github.io.shell-extension.zip
gnome-extensions enable aiu-indicator@citizenu03bb.github.io   # après déconnexion / reconnexion
```

`make test` lance les tests unitaires hors ligne (ceux de la CI) ; `make test-live` interroge le vrai `aiu`, donc vos fournisseurs, et ne tourne qu'en local.

GNOME Shell garde le JavaScript d'une extension en mémoire pour toute la session : après une mise à jour, déconnectez-vous puis reconnectez-vous (`make reload` ne recharge pas les modules).

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
