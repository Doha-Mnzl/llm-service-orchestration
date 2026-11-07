# Méthode de travail / Workflow

> 🇬🇧 English below.

## Français

1. **Chaque membre commite depuis son propre compte GitHub.** L'enseignant voit ainsi les
   contributions de chacun (Insights → Contributors).
2. **Une branche par tâche :** `feature/service-ventes`, `fix/timeout-rabbitmq`…
3. **Pull request vers `main`**, relue et fusionnée par un autre membre. Pas de push direct sur `main`.
4. **Petits commits fréquents**, avec des messages clairs :
   `ajoute l'endpoint des tendances par catégorie`, `gère le timeout RabbitMQ`.
5. **Une issue par tâche**, assignée à un membre, suivie dans le tableau GitHub Projects.
6. Lancer `pytest` avant d'ouvrir une pull request.

## English

1. **Each member commits from their own GitHub account**, so the professor can see individual
   contributions (Insights → Contributors).
2. **One branch per task:** `feature/sales-service`, `fix/rabbitmq-timeout`…
3. **Pull request into `main`**, reviewed and merged by another member. No direct pushes to `main`.
4. **Small, frequent commits** with clear messages.
5. **One issue per task**, assigned to a member and tracked on the GitHub Projects board.
6. Run `pytest` before opening a pull request.
