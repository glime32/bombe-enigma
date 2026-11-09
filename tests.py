#!/usr/bin/env python3
"""Tests rapides (quelques secondes) : la machine sur des cas connus, puis la
bombe sur un seul ordre de rotors et une partie des positions de départ."""
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bombe as b

resultats = []


def verifie(nom, ok, detail=""):
    resultats.append(bool(ok))
    print(f"  {'ok   ' if ok else 'ÉCHEC'} {nom}{'' if ok or not detail else ' : ' + detail}")


def machine(rotors, anneaux, positions, fiches=""):
    return b.Enigma(tuple(rotors.split()), "B", tuple(b.nombres(anneaux)), tuple(b.nombres(positions)),
                    [tuple(b.nombres(p)) for p in fiches.split()])


t0 = time.time()

# la machine
verifie("AAAAA en I-II-III, AAA : BDZGO", machine("I II III", "AAA", "AAA").chiffre("AAAAA") == "BDZGO")

encoches = tuple(b.ALPHA.index(b.ROTORS[r][1]) for r in ("I", "II", "III"))
pos, suite = tuple(b.nombres("ADU")), []
for _ in range(3):
    pos = b.avance(pos, encoches)
    suite.append(b.lettres(pos))
verifie("double pas du rotor du milieu : ADU → ADV → AEW → BFX", suite == ["ADV", "AEW", "BFX"], " → ".join(suite))

barbarossa = machine("II IV V", "BUL", "BLA", "AV BS CG DL FU HZ IN KM OW RX").chiffre(
    "EDPUDNRGYSZRCXNUYTPOMRMBOFKTBZREZKMLXLVEFGUEYSIOZVEQMIKUBPMMYLKLTTDEISMDICAGYKUACTCDOMOHWXMUU"
    "IAUBSTSLRNBZSZWNRFXWFYSSXJZVIJHIDISHPRKLKAYUPADTXQSPINQMATLPIFSVKDASCTACDPBOPVHJK")
verifie("vrai message de 1941 (opération Barbarossa) déchiffré",
        barbarossa.startswith("AUFKLXABTEILUNGXVONXKURTINOWA") and barbarossa.endswith("XANGRIFFXINFXRGTX"), barbarossa[:30])

rng = random.Random(3)
involution = lettres_jamais_elles = True
for _ in range(20):
    lettres_m = rng.sample(range(26), 20)
    m = b.Enigma(tuple(rng.sample(list(b.ROTORS), 3)), rng.choice("BC"), tuple(rng.randrange(26) for _ in range(3)),
                 tuple(rng.randrange(26) for _ in range(3)), list(zip(lettres_m[::2], lettres_m[1::2])))
    clair = "".join(rng.choice(b.ALPHA) for _ in range(400))
    chiffre = m.chiffre(clair)
    involution &= m.chiffre(chiffre) == clair
    lettres_jamais_elles &= all(x != y for x, y in zip(clair, chiffre))
verifie("chiffrer deux fois redonne le texte (20 réglages au hasard)", involution)
verifie("une lettre n'est jamais chiffrée en elle-même", lettres_jamais_elles)

# la bombe : le message de la démo (README), réglage I-II-IV, anneaux ZHN, départ FVF
secret = machine("I II IV", "ZHN", "FVF", "AH DI EJ GZ KR MY NS PT QV UW")
clair = b.nettoie(b.MESSAGES[1])
chiffre_txt = secret.chiffre(clair)
crib, chiffre = b.nombres("BULLETINMETEO"), b.nombres(chiffre_txt)
aretes = b.menu(crib, chiffre, 0)
verifie("menu du crib BULLETINMETEO : au moins 3 boucles", aretes and b.nb_boucles(aretes)[0] >= 3)

# décalages (position − anneau) pendant la 1re lettre du crib, comme les note la bombe
p0 = b.avance(secret.positions, secret.encoches)
vrai = sum(((p0[i] - secret.anneaux[i]) % 26) * 26 ** (2 - i) for i in range(3))
autres = random.Random(5).sample([x for x in range(26 ** 3) if x != vrai], 1500)
arrets = b.bombe_un_ordre((secret.rotors, "B", aretes, len(crib)), positions=[vrai] + autres)
vrais_brouilleurs = secret.brouilleurs(len(crib))
S = b.table_brouilleur(secret.rotors)
bons = [a for a in arrets if all(np.array_equal(S[a[1][0], (a[1][1] + (i > a[2])) % 26, (a[1][2] + i) % 26],
                                                vrais_brouilleurs[i]) for i in range(len(crib)))]
verifie("la bombe s'arrête sur la vraie position", bons, f"{len(arrets)} arrêt(s), aucun bon")
verifie("et écarte presque toutes les autres (≤ 5 faux arrêts sur 1500)", len(arrets) - len(bons) <= 5,
        f"{len(arrets) - len(bons)} faux arrêts")
if bons:
    fiches = bons[0][3]
    verifie("les fiches déduites par la bombe sont les vraies",
            fiches and all(secret.tableau[x] == y for x, y in fiches), str(fiches))

    langue = b.Langue(os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus.txt"))
    candidats = b.finition(bons[0], chiffre, crib, 0, langue)
    finis = [b.complete_tableau((c, chiffre, crib, 0, langue, True)) for c in candidats]
    note, trouvee, _ = max(finis, key=lambda r: r[0])
    verifie("anneaux et tableau complétés : tout le message déchiffré", trouvee.chiffre(chiffre_txt) == clair, str(trouvee))

print(f"{sum(resultats)} / {len(resultats)} en {time.time() - t0:.1f} s")
sys.exit(0 if all(resultats) else 1)
