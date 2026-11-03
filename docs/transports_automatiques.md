# Camions automatiques : création, créneaux, cycle de vie, annonce

Proposition du 03/11/2026, à valider avant implémentation. Maquettes : https://claude.ai/artifact/NEuPb22VKnjVASSv9GmLvH
(planning avec bande « À annoncer », référentiel, quais × créneaux, e-mail Outlook, tableau du cycle de vie).

Point de départ : un camion = un identifiant EM, l'heure importe peu au fournisseur mais les quais ont une
capacité (deux camions par quai et par créneau de 30 minutes, de 07:00 à 15:30). Aujourd'hui l'approvisionneur
crée chaque transport à la main (cocher, dialogue, date, heure, quais) puis envoie CSV et e-mail un par un.
Objectif : il ne crée plus rien hors MAF, il **annonce** et il **traite les exceptions**.

## 1. Qui est automatique

* Nouvelle colonne fournisseur **Camions automatiques** (`transport_auto`, oui / non, non par défaut à la
  livraison puis oui par lot sur les fournisseurs choisis), valable pour les modes *Fournisseur* et
  *Transporteur*. Sans objet pour *MAF Hambach* (navette multi-fournisseurs, manuelle) et *Messagerie* (pas de
  camion). Modifiable dans le Référentiel (cellule à un clic, lot) et dans Fiche fournisseur › Paramétrage.
* Nouvelle colonne **Créneau préféré** (`slot_preference` : indifférent / matin / après-midi), simple préférence
  pour l'ordonnanceur, jamais une contrainte.
* Règles communes : **horizon** des camions automatiques (`transport_auto_weeks`, 4 semaines), **grille** des
  créneaux (`dock_slot_start` 07:00, `dock_slot_end` 15:30, `dock_slot_minutes` 30), **capacité**
  (`dock_slot_capacity` 2 camions par quai et par créneau), **réannonce sur changement de quantité** (non).

## 2. Règle de création

À chaque synchronisation des données (changement de version), puis à la demande (« Mettre à jour les camions »),
pour chaque fournisseur automatique et chaque jour ouvré de l'horizon :

* **un camion par fournisseur et par jour de livraison** dès qu'au moins un ordre ferme ERP est attendu ce
  jour-là ; il emporte tous les créneaux fermes du jour (l'ERP reste la source : pas de camion sans commande) ;
* identifiant EM suivant (compteur partagé), quais par défaut du fournisseur, transporteur déduit du mode,
  créneau posé par l'ordonnanceur (§ 3), origine `auto`, état d'annonce « à annoncer » ;
* un ordre ferme sur un jour fermé (usine, fournisseur, transit) suit la règle des jours de livraison possibles
  déjà en place : il est signalé, pas déplacé ;
* un camion n'est pas scindé automatiquement par volume : la capacité d'un camion n'est pas connue de
  l'application ; l'approvisionneur scinde s'il le sait.

Idempotence : la règle est une **réconciliation** (état attendu ↔ camions existants), jamais une création
aveugle ; elle est exécutée une fois par version des données, sous verrou, et journalisée (combien créés,
modifiés, annulés). Les camions manuels (MAF, fournisseurs en manuel) occupent les mêmes créneaux et sont
respectés par l'ordonnanceur, jamais touchés.

## 3. Ordonnanceur des créneaux

* Grille par jour et par quai : 18 créneaux (07:00, 07:30, …, 15:30), capacité 2 par créneau. Un camion
  multi-quais prend une place sur chacun de ses quais au même créneau. Un quai fermé ce jour (calendrier) n'a
  aucun créneau.
* Ordre de traitement d'un jour : 1) les camions **annoncés** gardent leur créneau ; 2) les camions
  **épinglés** par l'approvisionneur (créneau, quai ou jour choisis à la main) gardent le leur ; 3) les autres,
  par préférence (matin / après-midi / indifférent) puis par code fournisseur, pour un résultat stable d'une
  synchronisation à l'autre.
* Placement : premier créneau libre sur tous les quais du camion, dans la demi-journée préférée, sinon dans
  l'autre ; aucun créneau libre → posé à 15:30 en **surcharge** et signalé (à déplacer d'un clic, autre créneau
  ou autre quai).
* Un camion **non annoncé** peut être replacé par l'ordonnanceur (par exemple si un quai ferme) ; un camion
  **annoncé** ne bouge jamais sans l'approvisionneur.
* Vue de contrôle **Quais × créneaux** d'une journée (depuis l'en-tête du jour dans le planning) : occupation,
  surcharges, quais fermés.

## 4. Cycle de vie (ce que fait chaque synchronisation)

| Événement | Effet | État d'annonce |
|---|---|---|
| ordre ferme sur un jour sans camion | création | à annoncer |
| ordre ferme sur un jour qui a son camion | il monte dans le premier camion non annulé du jour | modifié si le camion était annoncé |
| ordre déplacé dans l'ERP | retiré de l'ancien camion, ajouté au camion du nouveau jour (créé si besoin) | les deux passent modifié s'ils étaient annoncés |
| dernier ordre disparu ou soldé avant la date | camion annulé « plus aucune commande » ; l'EM n'est jamais réutilisé | annulation à annoncer s'il était annoncé, sinon il disparaît |
| quantité d'un ordre changée, même date | quantité mise à jour | rien (réglable) |
| ordre apparu pour demain ou aujourd'hui | créé quand même | **tardif**, en tête de la bande |
| plus de créneau libre | posé à 15:30 | **surcharge** |
| quai fermé (calendrier) | non annoncé : replacé sur un autre quai par défaut ; annoncé : signalé | **quai fermé** |
| scission, déplacement, changement de quai ou de créneau par l'approvisionneur | camion **épinglé** : la synchronisation n'y touche plus, sauf pour retirer un ordre disparu | modifié s'il était annoncé |
| annulation d'un camion automatique par l'approvisionneur | ses commandes restent « sans camion » ce jour-là ; recréé seulement si une nouvelle commande arrive | – |
| date passée | camion figé | – |

Actions de l'approvisionneur sur un camion (menu d'un clic dans la cellule) : voir les commandes, **scinder**
en deux camions (choisir les commandes du second, nouvel EM, nouveau créneau), changer le créneau ou les quais,
déplacer à un autre jour, ajouter une note pour le fournisseur, annuler. Plus de dialogue de création pour un
fournisseur automatique ; « + Créer le camion » ne reste que pour les fournisseurs en manuel et la navette MAF
(dialogue actuel, créneau proposé par l'ordonnanceur et modifiable).

## 5. Annonce et réannonce par e-mail (Outlook, sans serveur de mail)

Principe : **un e-mail par destinataire**, qui contient **tout ce qui a changé depuis sa dernière annonce** et
rien d'autre. Le destinataire est le groupe de contacts transport du fournisseur (mode *Fournisseur*) ou les
contacts du transporteur (mode *Transporteur*, un e-mail couvre alors plusieurs fournisseurs).

* **Bande « À annoncer »** en tête du planning : une ligne par destinataire avec le nombre de nouveaux, modifiés,
  annulés, les identifiants concernés, et une alerte « demain, à annoncer avant 15:00 » (heure réglable) ; les
  tardifs en tête. Quand tout est annoncé, la bande dit « Tout est annoncé ».
* **Bouton « Annoncer »** : ouvre dans Outlook le brouillon prêt (À = contacts, Cc = l'approvisionneur, objet,
  corps) et, dans le même geste, marque les camions **annoncés** en mémorisant l'état envoyé (date, heure, quais,
  liste des commandes, numéro d'annonce). Un toast « Annuler le marquage » pendant une minute couvre le cas où
  Outlook ne s'est pas ouvert. Rien n'est envoyé par l'application : c'est l'approvisionneur qui clique Envoyer.
* **Contenu** (maquette 4) : objet « [Usine] Livraisons <fournisseur> · S46-S47 · 3 camions (2 nouveaux,
  1 modifié) » ; trois sections courtes, *Nouveaux camions* (EM, date, heure, quai, commandes), *Camion modifié*
  (ce qui change : commande ajoutée ou retirée, nouvelle date, nouveau quai), *Camion annulé* ; une phrase « les
  créneaux ci-dessous remplacent toute annonce précédente pour les mêmes identifiants » ; un résumé en anglais ;
  un pied « annonce n° 7, générée le … ». Pas de pièce jointe : le CSV reste pour l'ATR.
* **Réannonce** : déclenchée seulement par un changement qui compte pour le fournisseur, date, heure, quai,
  commande ajoutée ou retirée, annulation. Une quantité qui bouge ne réannonce pas (réglable). Le camion revient
  dans la bande avec l'étiquette « modifié » et l'e-mail suivant ne reprend que lui, avec le détail du
  changement. Les camions inchangés ne sont jamais réécrits.
* **Longueur** : un e-mail `mailto:` reste fiable jusqu'à environ 1 500 à 2 000 caractères. Au-delà,
  l'application découpe en plusieurs e-mails (par semaine) et le dit ; à valider sur le poste de travail : un
  fichier `.eml` marqué `X-Unsent: 1` qui s'ouvre comme brouillon dans Outlook classique, sans limite de taille
  et avec tableau HTML, ce qui serait plus simple si l'environnement l'accepte.
* **ATR** : le CSV global des transports à venir reste l'export quotidien (bouton), avec un rappel « dernier
  export ce matin 07:42 » et, en option, un CSV « modifiés depuis le dernier export ». L'intégration directe
  viendra ensuite sans changer le reste.

## 6. La routine de l'approvisionneur, après

1. Ouvrir le planning : la bande « À annoncer » dit ce qu'il reste à faire, tardifs en tête.
2. Cliquer « Annoncer » destinataire par destinataire, envoyer dans Outlook. Deux à cinq clics par jour.
3. Exporter le CSV ATR une fois par jour (jusqu'à l'intégration).
4. Traiter les exceptions signalées en rouge : surcharge (déplacer), quai fermé, tardif (prévenir), et les
   demandes des fournisseurs (scinder, changer de jour).
5. Rien d'autre : les camions se créent, se complètent et s'annulent seuls en suivant l'ERP.

## 7. Données et API (pour l'implémentation)

* `ref_suppliers` : `transport_auto` (bool), `slot_preference` (texte). Règles communes `transport_auto_weeks`,
  `dock_slot_start`, `dock_slot_end`, `dock_slot_minutes`, `dock_slot_capacity`, `announce_deadline_time`,
  `reannounce_on_qty`.
* `app_transports` : `origin` (`auto` / `manuel`), `pinned` (bool), `flags` (`tardif`, `surcharge`,
  `quai_ferme`), `announce_state` (`a_annoncer`, `annonce`, `modifie`, `annulation_a_annoncer`, `annule_annonce`),
  `announced_at`, `announced_by`, `announced_snapshot` (JSON : date, heure, quais, commandes), `announce_no`.
* `app_transport_days_suppressed` : (fournisseur, jour, empreinte des commandes) pour les jours où un camion
  automatique a été annulé à la main.
* Service `transports.reconcile(version)` appelé au changement de version (verrou, une fois) et par
  `POST /api/transports/reconcile` ; `GET /api/transports/announce?recipient=` (brouillon : destinataires, objet,
  corps, identifiants) et `POST /api/transports/announce` (marquage) ; `GET /api/transports/docks?date=` (quais ×
  créneaux) ; `POST /api/transports/{id}/split|move|pin|cancel`.
* Journal : chaque réconciliation écrit une ligne (créés, modifiés, annulés) ; chaque annonce une ligne avec le
  numéro et les identifiants ; les camions ne sont pas annulables par Ctrl+Z (comme aujourd'hui).

## 8. Points à trancher

* L'horizon (4 semaines proposé) et l'heure limite d'annonce de la veille (15:00 proposé).
* Un changement de quantité doit-il réannoncer ? Proposé non.
* La semaine d'un camion annoncé dont le fournisseur demande un autre jour : déplacement épinglé, réannonce ;
  qui saisit la demande, l'approvisionneur seulement.
* Le `.eml` brouillon : à essayer sur un poste avant de choisir entre découpage mailto et fichier.
* Les fournisseurs passés en automatique à la première livraison : tous ceux en mode Fournisseur /
  Transporteur, ou une liste choisie (proposé : liste choisie, puis élargissement).
