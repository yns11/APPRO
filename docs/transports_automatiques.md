# Camions automatiques : création, créneaux, cycle de vie, plan de semaine

Proposition du 04/11/2026, **mise en œuvre le même jour** (règle de référence : docs/regles_metier.md § 9 ter bis ;
code : `backend/appro/services/transports_auto.py`). Maquettes : https://claude.ai/artifact/NEuPb22VKnjVASSv9GmLvH
(planning avec sa bande à deux lignes, référentiel, quais × créneaux, e-mail « plan de semaine », tableau du cycle de
vie). Écarts de mise en œuvre : le jour d'aujourd'hui est figé pour les retraits (un camion du jour est en route :
une commande disparue n'en sort pas, une commande apparue y monte et le marque « à vérifier ») ; un camion déplacé par
l'approvisionneur garde ses commandes même si l'ERP change leur date ; la bande n'affiche que la semaine en cours et
la suivante ; **pendant l'essai, le Planning de livraisons n'est visible que des e-mails de `APPRO_TRANSPORT_USERS`** ;
tout camion est scindable, un camion à une seule commande par **quantité** ; les écrans ont été refondus le 05/11
(vues Semaine, Quais, Charge, panneau de détail, page Envois : règles métier § 9 ter bis).

Point de départ : un camion = un identifiant EM ; l'heure importe peu au fournisseur mais les quais ont une capacité
(deux camions par quai et par créneau de 30 minutes, de 07:00 à 15:30). Aujourd'hui l'approvisionneur crée chaque
transport à la main puis envoie CSV et e-mail un par un. Objectif : il ne crée plus rien hors MAF, il **envoie le
plan de la semaine suivante le jeudi**, il **renvoie** une semaine quand elle change, il **vérifie** les cases rouges.

## 1. Qui est automatique

* Une seule case par fournisseur : **Camions automatiques** (`transport_auto`, non par défaut, cochée par lot sur les
  fournisseurs choisis), pour les modes *Fournisseur* et *Transporteur*. Sans objet pour *MAF Hambach* (navette
  multi-fournisseurs, manuelle) et *Messagerie* (pas de camion). Référentiel (cellule à un clic, lot) et Fiche
  fournisseur › Paramétrage.
* Rien d'autre à saisir : l'**heure habituelle** d'un fournisseur est celle de son dernier camion ; l'ordonnanceur la
  reprend chaque jour tant que le quai le permet. Elle s'affiche, elle ne se paramètre pas.
* Règles communes : grille des créneaux (`dock_slot_start` 07:00, `dock_slot_end` 15:30, `dock_slot_minutes` 30),
  capacité (`dock_slot_capacity` 2 camions par quai et par créneau), jour d'envoi du plan de semaine
  (`transport_plan_weekday`, jeudi), renvoi sur changement de quantité (`transport_resend_on_qty`, non).

## 2. Création

À chaque synchronisation des données (changement de version), pour chaque fournisseur automatique et chaque jour
ouvré des **trois semaines du planning** (semaine en cours, suivante, celle d'après) :

* **un camion par fournisseur et par jour** dès qu'au moins un ordre ferme ERP est attendu ce jour-là ; il emporte
  tous les créneaux fermes du jour. L'ERP reste la source : pas de camion sans commande ;
* identifiant EM suivant (compteur partagé), quais par défaut du fournisseur, transporteur déduit du mode, créneau
  posé par l'ordonnanceur (§ 3), origine `auto` ;
* pas de scission automatique par volume : la capacité d'un camion n'est pas connue de l'application ;
  l'approvisionneur scinde s'il le sait ;
* la règle est une **réconciliation** (état attendu ↔ camions existants), exécutée une fois par version des données,
  sous verrou, journalisée (créés, complétés, annulés). Les camions manuels (MAF, fournisseurs en manuel) occupent
  les mêmes créneaux et sont respectés, jamais touchés.

## 3. Ordonnanceur des créneaux

* Grille par jour et par quai : 18 créneaux (07:00, 07:30, …, 15:30), 2 camions par créneau. Un camion multi-quais
  prend une place sur chacun de ses quais au même créneau. Un quai fermé ce jour-là (calendrier) n'a aucun créneau.
* Ordre de traitement d'un jour : 1) les camions d'une **semaine déjà envoyée** gardent leur créneau ; 2) les camions
  **touchés à la main** gardent le leur ; 3) les autres, par code fournisseur, pour un résultat stable d'une
  synchronisation à l'autre.
* Placement : l'heure habituelle du fournisseur si tous ses quais ont une place, sinon le créneau libre le plus
  proche (avant ou après) ; aucune place → posé à 15:30 et marqué **à vérifier** (« B15 complet : déplacer »).
* Un camion d'une semaine non envoyée peut encore être replacé par l'ordonnanceur ; un camion d'une semaine
  envoyée ne bouge jamais sans l'approvisionneur.
* Vue de contrôle **Quais × créneaux** d'une journée (depuis l'en-tête du jour) : occupation, cases « à vérifier »,
  quais fermés.

## 4. Cycle de vie (à chaque synchronisation)

| Événement | Effet automatique | Côté annonce |
|---|---|---|
| ordre ferme sur un jour sans camion | création | rien si la semaine n'est pas envoyée ; sinon ✱, semaine « à renvoyer » |
| ordre ferme sur un jour qui a son camion | il monte dans le camion du jour | idem |
| ordre qui quitte un jour (date changée dans l'ERP, soldé, supprimé) | retiré du camion ; camion annulé s'il est vide, l'EM n'est jamais réutilisé | idem ; le camion annulé reste visible barré jusqu'au renvoi |
| quantité d'un ordre changée | quantité mise à jour | rien (réglable) |
| pas de créneau libre, quai fermé, ordre apparu pour demain ou aujourd'hui | camion créé quand même, posé au mieux | **à vérifier**, raison écrite dans la cellule |
| scission, déplacement, changement d'heure ou de quai par l'approvisionneur | l'application garde le choix et n'y touche plus, sauf pour retirer un ordre disparu | ✱ si la semaine était envoyée |
| annulation d'un camion automatique par l'approvisionneur | ses commandes restent « sans camion » ce jour-là ; un camion renaît seulement si une nouvelle commande arrive | ✱ si la semaine était envoyée |
| semaine passée | camions figés | plus d'envoi ; les réceptions se lisent dans l'OTD |

Il n'y a pas de notion d'« ordre déplacé » : un ordre dont l'ERP change la date disparaît d'un jour et apparaît à
un autre, chaque jour est réconcilié séparément.

**Déclenche un ✱** : jour, heure, quai, commande ajoutée ou retirée, camion créé ou annulé. **Ne déclenche rien** :
une quantité, une note, tout changement au-delà de la semaine suivante.

Actions de l'approvisionneur sur un camion (menu d'un clic dans la cellule) : voir les commandes, **scinder** en deux
camions (choisir les commandes du second, nouvel EM, nouveau créneau), changer le jour, l'heure ou le quai, note
pour le fournisseur, annuler. Plus de dialogue de création pour un fournisseur automatique ; « + Camion » ne reste
que pour les fournisseurs en manuel et la navette MAF (dialogue actuel, créneau proposé et modifiable).

## 5. Le plan de semaine : envoi et renvoi par e-mail (Outlook, sans serveur de mail)

**Principe** : un e-mail = **une semaine** = **un destinataire**, toujours **complet**. Jamais de delta à recouper.
Le destinataire est le groupe de contacts transport du fournisseur (mode *Fournisseur*) ou les contacts du
transporteur (mode *Transporteur* : un e-mail couvre alors ses fournisseurs, un bloc par fournisseur).

* **Le jeudi** (jour réglable), la bande du planning dit « Plan de la semaine 47 : 9 destinataires, 31 camions »
  avec une pastille par destinataire et un bouton **Envoyer le suivant**. Chaque clic ouvre dans Outlook le
  brouillon prêt (À, Cc l'approvisionneur, objet, corps) et note la semaine **envoyée** à ce destinataire, avec
  l'état envoyé (camions, jours, heures, quais, commandes) et un numéro d'envoi. L'application n'envoie rien :
  c'est l'approvisionneur qui clique Envoyer dans Outlook. Si Outlook ne s'est pas ouvert, le bouton **Renvoyer**
  est toujours là.
* **Rectificatif** : dès qu'un ✱ touche une semaine déjà envoyée (la semaine en cours ou la suivante), cette semaine
  passe **à renvoyer** pour le destinataire concerné, sur la seconde ligne de la bande, avec le motif en clair
  (« EM000231 : commande ajoutée mar. 10/11 »). Le renvoi est **le même plan de semaine, entier**, titré « mise à
  jour du 10/11 (remplace l'envoi du 05/11) », les lignes changées marquées ✱, les camions annulés barrés. Rien à
  comprendre pour le fournisseur : le dernier mail reçu est la vérité.
* **Au-delà de la semaine suivante**, rien ne part : le changement sera dans le plan du jeudi suivant. C'est ce qui
  empêche l'accumulation.
* **Contenu** (maquette 4) : objet « [Usine] Livraisons semaine 46 · Geodis · mise à jour du 10/11 » ; une phrase
  (« ce plan remplace entièrement l'envoi du 05/11 ; identifiant du camion à donner aux chauffeurs ») ; un bloc par
  fournisseur : camion, jour, heure, commandes (référence, article, quantité) ; un résumé en anglais ; un pied
  « Plan S46 · envoi n° 2 · généré le … ». Pas de pièce jointe : le CSV reste pour l'ATR.
* **Longueur** : un `mailto:` reste fiable jusqu'à environ 1 500 à 2 000 caractères ; un plan de semaine d'un
  transporteur à plusieurs fournisseurs peut dépasser. Au-delà, l'application découpe par fournisseur (un e-mail
  par bloc) et le dit. À essayer sur un poste : un fichier `.eml` marqué `X-Unsent: 1`, qui s'ouvre comme brouillon
  dans Outlook classique sans limite de taille et avec un vrai tableau ; si l'environnement l'accepte, c'est le
  plus simple.
* **ATR** : le CSV global des transports à venir reste l'export quotidien (bouton, rappel de l'heure du dernier
  export). L'intégration directe viendra ensuite sans rien changer au reste.

## 6. La routine de l'approvisionneur, après

* **Jeudi** : la bande liste les destinataires de la semaine suivante. Un clic par destinataire, Envoyer dans Outlook.
  Neuf destinataires, neuf clics.
* **Les autres jours** : un coup d'œil à la bande. Zéro ou un rectificatif à renvoyer. Les cases rouges « à
  vérifier » à régler d'un clic (déplacer un camion, prévenir d'un camion tardif).
* **Une fois par jour** : exporter le CSV ATR, jusqu'à l'intégration.
* Rien d'autre : les camions se créent, se complètent et s'annulent seuls en suivant l'ERP.

## 7. Données et API (pour l'implémentation)

* `ref_suppliers` : `transport_auto` (bool). Règles communes `dock_slot_start`, `dock_slot_end`,
  `dock_slot_minutes`, `dock_slot_capacity`, `transport_plan_weekday`, `transport_resend_on_qty`.
* `app_transports` : `origin` (`auto` / `manuel`), `pinned` (bool, touché à la main), `check_reason` (texte, vide =
  rien à vérifier), `changed_since_sent` (bool, le ✱).
* `app_transport_plans` : (destinataire, semaine ISO) → `sent_no`, `sent_at`, `sent_by`, `snapshot` (JSON : camions,
  jours, heures, quais, commandes) ; état dérivé : à envoyer / envoyée / à renvoyer.
* `app_transport_days_suppressed` : (fournisseur, jour, empreinte des commandes) pour les jours où un camion
  automatique a été annulé à la main.
* Service `transports.reconcile(version)` au changement de version (verrou, une fois) ; `GET /api/transports/plans`
  (la bande : par semaine et destinataire, état, motifs) ; `GET /api/transports/plans/{week}/{recipient}/mail`
  (brouillon : destinataires, objet, corps, découpage éventuel) et `POST …/sent` (marquage, numéro d'envoi) ;
  `GET /api/transports/docks?date=` (quais × créneaux) ; `POST /api/transports/{id}/split|move|cancel`.
* Journal : une ligne par réconciliation (créés, complétés, annulés), une par envoi (semaine, destinataire,
  numéro) ; les camions restent non annulables par Ctrl+Z.

## 8. Points à trancher

* Le jour d'envoi du plan (jeudi proposé) et si la semaine en cours reçoit aussi des rectificatifs (proposé oui).
* Une quantité qui change doit-elle renvoyer ? Proposé non.
* Le `.eml` brouillon : à essayer sur un poste avant de choisir entre découpage par fournisseur et fichier.
* Les fournisseurs passés en automatique au départ : liste choisie, puis élargissement.
