# bombe-enigma

Une machine **Enigma** (modèle I / M3 de la Wehrmacht), et l'attaque qui l'a cassée pendant la guerre : la **bombe de Turing et Welchman**, réécrite en Python avec numpy.

L'attaque n'a besoin que du message chiffré et d'un **crib** : un morceau de texte clair qu'on devine. Les Allemands envoyaient chaque matin un bulletin météo qui commençait toujours pareil ; ici, `BULLETINMETEO`. À partir de là, le programme retrouve **l'ordre des rotors, leurs positions, les anneaux et le tableau de connexions**, puis déchiffre tout.

```
$ python3 bombe.py demo --graine 1
(56 message(s) écarté(s) : leur menu avait moins de 3 boucles)

réglage secret : rotors I-II-IV, réflecteur B, anneaux ZHN, départ FVF, fiches AH DI EJ GZ KR MY NS PT QV UW

message chiffré (148 lettres) :
TJITU UEHNT UVEUP GSKGT DRHGE UTHTH RRQKZ ECPLU OXQRZ BONJD WVYSK UNGYC FAOKU HEAYX ...

on suppose que le message commence par « BULLETINMETEO ».

menu : 13 arêtes, 12 lettres, 3 boucle(s) autour de la lettre testée
  bombe : 60/60 ordres, 40 arrêt(s) vérifié(s) en 48.4 s

BULLE TINME TEODU MATIN CIELD EGAGE SURLE SECTE URNOR DVENT FAIBL EDESU DMERC ALMEC ONDIT IONSF AVORA
BLESP OURLA SORTI EPREV UEAVA NTLAU BECON FIRME RLARE CEPTI ONDEC EMESS AGE

148/148 lettres justes, 49.5 s au total
```

## Utilisation

```sh
pip install numpy                     # seule dépendance
python3 bombe.py demo                 # réglage secret au hasard, chiffrement, attaque
python3 bombe.py chiffrer --rotors "II IV V" --anneaux "B U L" --positions "L E S" \
        --fiches "AV BS CG DL FU HZ IN KM OW RX" "Bulletin météo"
python3 bombe.py attaquer --crib BULLETINMETEO message.txt
```

Chiffrer et déchiffrer sont la même opération (Enigma est une involution). La machine est vérifiée sur les cas connus : `AAAAA` avec les rotors I-II-III en position AAA donne `BDZGO`, et la séquence `ADU → ADV → AEW → BFX` montre le célèbre « double pas » du rotor du milieu.

## La machine

Une lettre traverse le tableau de connexions (des fiches qui échangent des paires de lettres), trois rotors, un réflecteur, les trois rotors à l'envers, et à nouveau le tableau. Avant chaque lettre, le rotor de droite tourne d'un cran ; quand il passe son encoche, il entraîne celui du milieu, qui entraîne celui de gauche.

Deux conséquences dont l'attaque se sert :
- **une lettre n'est jamais chiffrée en elle-même** (à cause du réflecteur). Le programme élimine donc d'office les positions où le crib aurait une lettre en face d'elle-même ;
- seul le décalage `position − anneau` de chaque rotor compte pour le câblage : la bombe peut chercher les rotors sans connaître les anneaux.

## La bombe

**Le menu.** On aligne le crib sous le message chiffré : `B` en face de `N`, `U` en face de `L`... Chaque paire est une arête d'un graphe, étiquetée par sa position. Si `a` est relié à `x` par le tableau de connexions, alors à la position `i`, `b` est relié à `Sᵢ(x)`, où `Sᵢ` est la permutation des rotors à cet instant.

**L'hypothèse et la contradiction.** Pour une position de départ des rotors, on suppose une fiche pour la lettre la plus connectée du menu, et on propage les conséquences le long des arêtes. Le **tableau diagonal** de Welchman ajoute la symétrie (`a ↔ x` équivaut à `x ↔ a`), ce qui fait circuler l'information beaucoup plus loin. Si l'hypothèse est fausse, le « courant » finit par allumer les 26 fils d'une même lettre : contradiction, on élimine la position. Sinon c'est un **arrêt**.

**En numpy.** Les 17 576 positions de départ sont testées ensemble : un tableau booléen de forme `(17576, 26, 26)`, où `M[n, a, x]` signifie « à la position n, a est relié à x ». Une arête devient une seule opération vectorisée (`M[:, b, :] |= M[lignes, a, Sᵢ]`), le tableau diagonal un `M | M.transpose(0, 2, 1)`. Les positions contradictoires sont retirées au fur et à mesure.

**Le pas du rotor du milieu.** La vraie bombe supposait que le rotor du milieu ne bougeait pas pendant le crib, et ratait alors certains messages. Ici on essaie chaque instant possible où il peut avancer : 13 fois plus de travail. Seul le « double pas » (le milieu qui entraîne aussi le rotor de gauche) n'est pas prévu.

**Les boucles.** Une boucle dans le menu (une lettre qui revient à elle-même par un autre chemin) est un test que les mauvaises positions ratent presque toujours. Sans boucle, la bombe s'arrête partout et ne sert à rien : comme à Bletchley Park, le programme refuse un tel menu et demande un crib plus long ou placé ailleurs.

**La finition.** Un arrêt donne les décalages des rotors et une partie des fiches. Le programme essaie alors les anneaux compatibles, remonte jusqu'au début du message (en inversant le mouvement des rotors), et note chaque déchiffrement avec des statistiques de **bigrammes du français**, apprises sur [`corpus.txt`](corpus.txt). Les 1500 meilleurs candidats voient leur tableau de connexions complété par montée de colline (brancher ou débrancher chaque paire de lettres libres tant que le texte devient plus « français ») ; les 10 meilleurs ont ensuite droit à une recherche conjointe anneaux + tableau, et à une recherche à deux coups.

Le réglage trouvé peut différer du vrai par les anneaux et les positions tout en étant **équivalent** : il donne exactement le même texte. Seuls les décalages comptent pour le câblage, et l'anneau de gauche n'a aucun effet visible.

## Résultats

Sur 10 clés secrètes tirées au hasard (`demo --graine 1` à `10`, 8 cœurs) :

| | |
|---|---|
| message retrouvé à 100 % | **9 / 10** |
| temps par attaque | 47 à 68 s |
| arrêts à départager | 6 à 351 |

L'échec (graine 2) est un cas que la vraie bombe ratait aussi : pendant les 13 lettres du crib, le rotor du milieu fait son « double pas » et entraîne celui de gauche, ce que le modèle de la bombe ne prévoit pas.

Ce que les essais ratés m'ont appris, et qui est maintenant corrigé :
- avec un tableau de connexions incomplet, la note statistique choisit parfois l'anneau de droite décalé d'un cran (une ou deux lettres fausses toutes les 26) : les anneaux sont donc recherchés en même temps que le tableau, sur les meilleurs candidats ;
- la montée de colline sur le tableau peut rester coincée quand il faudrait changer deux fiches à la fois : les finalistes ont droit à une recherche à deux coups ;
- seules les boucles de la composante du menu où l'on fait l'hypothèse servent. Avec moins de 3 boucles, il reste des milliers d'arrêts : la démo écarte ces messages, comme le faisait Bletchley Park.

## Ordres de grandeur

La vraie bombe testait les 17 576 positions d'un ordre de rotors en une vingtaine de minutes, et il fallait en essayer 60. Ici, en Python vectorisé sur 8 cœurs, les 60 ordres et les 13 instants de pas du rotor du milieu prennent à peu près une minute.
