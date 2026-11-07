#!/usr/bin/env python3
"""
bombe — une machine Enigma, et la bombe de Turing-Welchman qui la casse.

  python3 bombe.py demo                          réglage secret au hasard, chiffrement, puis attaque
  python3 bombe.py chiffrer [réglage] TEXTE      chiffrer ou déchiffrer (c'est la même opération)
  python3 bombe.py attaquer --crib MOT FICHIER   retrouver le réglage à partir d'un mot probable

L'attaque n'a besoin que du message chiffré et d'un « crib » : un morceau de
texte clair qu'on devine (un en-tête de bulletin météo, une formule de
politesse...). Elle retrouve l'ordre des rotors, leurs positions, les anneaux
et le tableau de connexions.
"""
import argparse
import functools
import itertools
import os
import random
import sys
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor

import numpy as np

ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ROTORS = {  # câblage, lettre d'encoche (le rotor suivant avance quand celui-ci quitte cette lettre)
    "I": ("EKMFLGDQVZNTOWYHXUSPAIBRCJ", "Q"),
    "II": ("AJDKSIRUXBLHWTMCQGZNPYFVOE", "E"),
    "III": ("BDFHJLCPRTXVZNYEIWGAKMUSQO", "V"),
    "IV": ("ESOVPZJAYQUIRHXLNFTGKDCMWB", "J"),
    "V": ("VZBRGITYUPSDNHLXAWMJQOFECK", "Z"),
}
REFLECTEURS = {"B": "YRUHQSLDPXNGOKMIEBFZCWVJAT", "C": "FVPJIAOYEDRZXWGCTKUQSBNMHL"}


def nettoie(texte):
    """« Bulletin météo ! » -> 'BULLETINMETEO' (comme le faisaient les opérateurs)."""
    sans_accents = unicodedata.normalize("NFD", texte)
    return "".join(c for c in sans_accents.upper() if c in ALPHA)


def nombres(texte):
    return np.array([ALPHA.index(c) for c in nettoie(texte)], dtype=np.int64)


def lettres(nb):
    return "".join(ALPHA[int(i)] for i in nb)


# ----------------------------------------------------------------- la machine

def avance(pos, encoches):
    """Un appui de touche. pos = (gauche, milieu, droite), lettres affichées.
    Le rotor du milieu avance aussi quand il est lui-même sur son encoche :
    c'est le fameux « double pas » d'Enigma."""
    g, m, d = pos
    if m == encoches[1]:
        m, g = (m + 1) % 26, (g + 1) % 26
    elif d == encoches[2]:
        m = (m + 1) % 26
    return (g, m, (d + 1) % 26)


def recule(pos, encoches):
    """Inverse de avance() : l'état d'avant cet appui (None s'il n'existe pas)."""
    g, m, d = pos
    for dg, dm in ((0, 0), (0, 1), (1, 1)):
        avant = ((g - dg) % 26, (m - dm) % 26, (d - 1) % 26)
        if avance(avant, encoches) == pos:
            return avant
    return None


@functools.lru_cache(maxsize=8)
def table_brouilleur(ordre, reflecteur="B"):
    """S[dg, dm, dd, x] : lettre obtenue en envoyant x dans les trois rotors,
    le réflecteur, puis les rotors à l'envers, pour tous les décalages
    (position − anneau) des trois rotors. 26⁴ cases calculées d'un coup."""
    W = [np.array([ALPHA.index(c) for c in ROTORS[r][0]]) for r in ordre]
    Winv = [np.argsort(w) for w in W]
    U = np.array([ALPHA.index(c) for c in REFLECTEURS[reflecteur]])
    o = np.arange(26)
    dg, dm, dd, x = o[:, None, None, None], o[None, :, None, None], o[None, None, :, None], o[None, None, None, :]

    def passe(T, c, dec):
        return (T[(c + dec) % 26] - dec) % 26

    c = passe(W[2], x, dd)
    c = passe(W[1], c, dm)
    c = passe(W[0], c, dg)
    c = U[c]
    c = passe(Winv[0], c, dg)
    c = passe(Winv[1], c, dm)
    c = passe(Winv[2], c, dd)
    return c.astype(np.int64)


class Enigma:
    def __init__(self, rotors=("I", "II", "III"), reflecteur="B", anneaux=(0, 0, 0), positions=(0, 0, 0), fiches=()):
        self.rotors, self.reflecteur = tuple(rotors), reflecteur
        self.anneaux, self.positions = tuple(anneaux), tuple(positions)
        self.encoches = tuple(ALPHA.index(ROTORS[r][1]) for r in rotors)
        self.tableau = np.arange(26)
        for a, b in fiches:
            self.tableau[a], self.tableau[b] = b, a
        self.S = table_brouilleur(self.rotors, reflecteur)  # en cache : calculée une fois par ordre

    def brouilleurs(self, n, depart=None):
        """Les n permutations successives (une par lettre), sans le tableau."""
        pos, res = depart or self.positions, np.empty((n, 26), dtype=np.int64)
        for k in range(n):
            pos = avance(pos, self.encoches)
            res[k] = self.S[(pos[0] - self.anneaux[0]) % 26, (pos[1] - self.anneaux[1]) % 26, (pos[2] - self.anneaux[2]) % 26]
        return res

    def chiffre(self, texte):
        x = nombres(texte)
        P, S = self.tableau, self.brouilleurs(len(x))
        return lettres(P[S[np.arange(len(x)), P[x]]])

    def __str__(self):
        fiches = " ".join(ALPHA[a] + ALPHA[b] for a, b in enumerate(self.tableau) if a < b)
        return (f"rotors {'-'.join(self.rotors)}, réflecteur {self.reflecteur}, "
                f"anneaux {lettres(self.anneaux)}, départ {lettres(self.positions)}, fiches {fiches or '(aucune)'}")


# --------------------------------------------------------------------- la bombe

def menu(crib, chiffre, k):
    """Le « menu » : chaque lettre du crib et la lettre chiffrée en face forment
    une arête (a, b, i) d'un graphe. Enigma ne chiffre jamais une lettre en
    elle-même : si a == b quelque part, le crib n'est pas à cette place."""
    aretes = []
    for i, (a, b) in enumerate(zip(crib, chiffre[k:k + len(crib)])):
        if a == b:
            return None
        aretes.append((int(a), int(b), i))
    return aretes


def analyse_menu(aretes):
    """Le menu est un graphe (lettres, arêtes). Une boucle (une lettre qui
    revient à elle-même par un autre chemin) est un test que les mauvaises
    positions ratent presque toujours ; sans boucle, la bombe s'arrête à peu
    près partout. Seules comptent les boucles de la composante connexe où
    l'on fait l'hypothèse : on choisit donc la composante la plus bouclée,
    et dedans la lettre la plus connectée.
    Renvoie (lettre de test, boucles de sa composante, nombre de lettres)."""
    lettres_menu = sorted({a for a, b, i in aretes} | {b for a, b, i in aretes})
    parent = {x: x for x in lettres_menu}

    def racine(x):
        while parent[x] != x:
            x = parent[x]
        return x

    for a, b, i in aretes:
        parent[racine(a)] = racine(b)
    degre = {x: 0 for x in lettres_menu}
    for a, b, i in aretes:
        degre[a] += 1
        degre[b] += 1
    meilleur = None
    for r in {racine(x) for x in lettres_menu}:
        comp = [x for x in lettres_menu if racine(x) == r]
        n_aretes = sum(1 for a, b, i in aretes if racine(a) == r)
        boucles = n_aretes - len(comp) + 1  # nombre cyclomatique de la composante
        test = max(comp, key=lambda x: degre[x])
        cle = (boucles, len(comp), degre[test])
        if meilleur is None or cle > meilleur[0]:
            meilleur = (cle, test, boucles)
    return meilleur[1], meilleur[2], len(lettres_menu)


def nb_boucles(aretes):
    """(boucles utiles, nombre de lettres du menu)"""
    test, boucles, n = analyse_menu(aretes)
    return boucles, n


def propage(M, aretes, perms, test):
    """Fait circuler le courant dans la bombe jusqu'à stabilité.

    M[n, a, x] = « a est relié à x par le tableau » est une conséquence de
    l'hypothèse de départ, pour la position n. Une arête (a, b, i) dit :
    si a <-> x, alors b <-> S_i(x). Le « tableau diagonal » de Welchman
    ajoute la symétrie : a <-> x équivaut à x <-> a. Les positions dont le
    registre de test s'allume en entier sont contradictoires : on les jette
    au fur et à mesure pour ne garder que les arrêts."""
    garde = np.arange(M.shape[0])
    avant = None
    while len(garde):
        lignes = np.arange(len(garde))[:, None]
        for a, b, i in aretes:
            p = perms[i]
            M[:, b, :] |= M[lignes, a, p]  # M[n, b, y] |= M[n, a, S_i(y)]
            M[:, a, :] |= M[lignes, b, p]
        M = M | M.transpose(0, 2, 1)
        total = M.sum(axis=(1, 2))
        vivant = ~M[:, test, :].all(axis=1)
        if not vivant.all():
            M, garde, total = M[vivant], garde[vivant], total[vivant]
            perms = {i: p[vivant] for i, p in perms.items()}
            avant = avant[vivant] if avant is not None else None
        if avant is not None and np.array_equal(total, avant):
            break
        avant = total
    return M, garde


MAX_ARRETS = 5000  # par ordre de rotors ; au-delà, le menu ne trie plus rien


def bombe_un_ordre(args):
    """Fait tourner la bombe pour un ordre de rotors, sur les 17 576 positions
    de départ et pour chaque instant possible où le rotor du milieu avance
    pendant le crib. Renvoie les arrêts qui résistent à la vérification."""
    ordre, reflecteur, aretes, n = args
    S = table_brouilleur(ordre, reflecteur)
    idx = np.arange(26 ** 3)
    G, M_, D = idx // 676, (idx // 26) % 26, idx % 26
    test = analyse_menu(aretes)[0]
    arrets = []
    for j in range(n):  # le rotor du milieu avance après la lettre j du crib (j = n-1 : pas pendant le crib)
        perms = {i: S[G, (M_ + (i > j)) % 26, (D + i) % 26] for a, b, i in aretes}
        M = np.zeros((26 ** 3, 26, 26), dtype=bool)
        M[:, test, test] = True  # hypothèse : la lettre test n'a pas de fiche (n'importe laquelle irait)
        M, garde = propage(M, aretes, perms, test)
        # Vérification de tous les arrêts d'un coup : pour chaque arrêt, on
        # relance la bombe avec l'hypothèse « test <-> h » que l'arrêt désigne,
        # et on exige qu'aucune lettre n'ait deux fiches.
        pos_c, h_c = [], []
        for ligne, n_pos in zip(M, garde):
            allumes = ligne[test]
            for h in (np.flatnonzero(allumes) if allumes.sum() <= 13 else np.flatnonzero(~allumes)):
                pos_c.append(n_pos)
                h_c.append(h)
        if not pos_c or len(pos_c) > 50_000:  # aucun arrêt, ou menu bien trop faible pour être utile
            continue
        pos_c, h_c = np.array(pos_c), np.array(h_c)
        m = np.zeros((len(pos_c), 26, 26), dtype=bool)
        m[np.arange(len(pos_c)), test, h_c] = True
        m[np.arange(len(pos_c)), h_c, test] = True
        m, ok = propage(m, aretes, {i: p[pos_c] for i, p in perms.items()}, test)
        for mk, k in zip(m, ok):
            if len(arrets) >= MAX_ARRETS:  # menu trop faible : on n'en garde pas plus
                break
            if (mk.sum(axis=1) <= 1).all():
                n_pos = pos_c[k]
                fiches = [(a, int(np.flatnonzero(mk[a])[0])) for a in range(26) if mk[a].any()]
                arrets.append((ordre, (int(G[n_pos]), int(M_[n_pos]), int(D[n_pos])), j, fiches))
    return arrets


# ---------------------------------------------------- finition : anneaux et fiches

class Langue:
    """Statistiques de bigrammes du français, apprises sur corpus.txt."""

    def __init__(self, chemin):
        t = nombres(open(chemin, encoding="utf-8").read())
        c = np.ones((26, 26))
        np.add.at(c, (t[:-1], t[1:]), 1)
        self.log = np.log(c / c.sum(axis=1, keepdims=True))

    def note(self, x):
        return float(self.log[x[:-1], x[1:]].sum()) / max(1, len(x) - 1)


def finition(arret, chiffre, crib, k, langue):
    """Un arrêt donne les décalages (position − anneau) au début du crib et une
    partie du tableau. On essaie les anneaux compatibles, on remonte jusqu'au
    début du message, et on garde le déchiffrement le plus « français ».
    Puis on complète le tableau en essayant d'ajouter des fiches."""
    ordre, (dg, dm, dd), j, fiches = arret
    n = len(crib)
    encoches = tuple(ALPHA.index(ROTORS[r][1]) for r in ordre)
    S = table_brouilleur(ordre)
    P0 = np.arange(26)
    for a, b in fiches:
        P0[a], P0[b] = b, a
    if j < n - 1:  # le pas du milieu tombe après la lettre j : l'anneau de droite est connu
        anneaux_d = [(encoches[2] - (dd + j)) % 26]
    else:          # sinon : tous ceux qui ne font pas avancer le milieu pendant le crib
        anneaux_d = [r for r in range(26) if all((dd + i + r) % 26 != encoches[2] for i in range(n - 1))]
    # seules les lettres du crib dont la bombe a trouvé la fiche peuvent être
    # vérifiées tout de suite (une partie du menu peut être déconnectée)
    connues = {a for a, b in fiches}
    a_verifier = np.array([i for i in range(n) if crib[i] in connues and chiffre[k + i] in connues], dtype=np.int64)
    candidats = []
    for rd, rm in itertools.product(anneaux_d, range(26)):
        anneaux = (0, rm, rd)
        etat = ((dg + 0) % 26, (dm + rm) % 26, (dd + rd) % 26)  # affiché pendant la 1re lettre du crib
        for _ in range(k + 1):
            etat = recule(etat, encoches)
            if etat is None:
                break
        if etat is None:
            continue
        machine = Enigma(ordre, "B", anneaux, etat)
        Sm = machine.brouilleurs(len(chiffre))
        clair = P0[Sm[np.arange(len(chiffre)), P0[chiffre]]]
        if not np.array_equal(clair[k + a_verifier], crib[a_verifier]):
            continue
        candidats.append((langue.note(clair), anneaux, etat, clair.tobytes()))
    # les 3 meilleurs réglages d'anneaux de cet arrêt, notés avec le tableau partiel ;
    # beaucoup donnent exactement le même texte (l'anneau du milieu ne compte souvent
    # pas), on ne garde donc que des déchiffrements différents
    candidats.sort(key=lambda c: -c[0])
    vus, garde = set(), []
    for note, anneaux, etat, texte in candidats:
        if texte not in vus:
            vus.add(texte)
            garde.append((note, ordre, anneaux, etat, fiches))
        if len(garde) == 3:
            break
    return garde


def complete_tableau(args):
    """Termine le réglage d'un candidat, en alternant deux étapes :
    1. montée de colline sur les fiches que la bombe n'a pas fixées : on
       essaie de brancher (ou débrancher) chaque paire de lettres libres et
       on garde tout changement qui rend le texte plus « français » ;
    2. avec ce tableau complet, on réessaie tous les anneaux : le choix fait
       avec un tableau partiel peut être décalé d'un cran (le rotor du milieu
       avance alors une lettre trop tôt ou trop tard, toutes les 26 lettres)."""
    (note, ordre, anneaux, etat, fiches), chiffre, crib, k, langue, avec_anneaux = args
    n = len(crib)
    encoches = tuple(ALPHA.index(ROTORS[r][1]) for r in ordre)
    fixes = {a for a, b in fiches} | {b for a, b in fiches}
    P = np.arange(26)
    for a, b in fiches:
        P[a], P[b] = b, a
    rang = np.arange(len(chiffre))
    # décalages (position − anneau) au début du crib : ils ne changent pas quand on change les anneaux
    pos = etat
    for _ in range(k + 1):
        pos = avance(pos, encoches)
    decalages = tuple((pos[i] - anneaux[i]) % 26 for i in range(3))

    def evalue(P, Sm):
        clair = P[Sm[rang, P[chiffre]]]
        return langue.note(clair) + 10 * np.array_equal(clair[k:k + n], crib)

    def fiches_par_colline(P, Sm):
        note = evalue(P, Sm)
        mieux = True
        while mieux:
            mieux = False
            libres = [x for x in range(26) if x not in fixes]
            for a, b in itertools.combinations(libres, 2):
                Q = P.copy()
                for x in (a, b):
                    if Q[x] != x:
                        Q[Q[x]], Q[x] = Q[x], x
                Q[a], Q[b] = b, a
                for essai in (Q,) + ((P.copy(),) if P[a] == b else ()):
                    if essai is not Q:  # essayer aussi de retirer la fiche a-b
                        essai[a], essai[b] = a, b
                    s = evalue(essai, Sm)
                    if s > note + 1e-9:
                        P, note, mieux = essai, s, True
        return P, note

    def branche(P, a, b):
        Q = P.copy()
        for x in (a, b):
            if Q[x] != x:
                Q[Q[x]], Q[x] = Q[x], x
        Q[a], Q[b] = b, a
        return Q

    def deux_coups(P, Sm):
        """La montée de colline peut rester bloquée quand il faudrait changer
        deux fiches à la fois : on essaie toutes les paires de modifications."""
        libres = [x for x in range(26) if x not in fixes]
        coups = list(itertools.combinations(libres, 2))
        note = evalue(P, Sm)
        meilleur = (note, P)
        for a, b in coups:
            P1 = branche(P, a, b)
            for c, d in coups:
                if (c, d) <= (a, b):
                    continue
                P2 = branche(P1, c, d)
                s2 = evalue(P2, Sm)
                if s2 > meilleur[0] + 1e-9:
                    meilleur = (s2, P2)
        return meilleur[1], meilleur[0]

    Sm = Enigma(ordre, "B", anneaux, etat).brouilleurs(len(chiffre))
    if avec_anneaux:
        # Recherche conjointe : pour chaque réglage d'anneaux qui donne un
        # déchiffrement différent (avec les fiches de la bombe), on complète le
        # tableau et on garde le meilleur. L'anneau du milieu ne se voit qu'au
        # moment où le rotor de gauche bouge : impossible de le choisir avant
        # d'avoir un tableau complet.
        connues = {a for a, b in fiches}
        verif = np.array([i for i in range(n) if crib[i] in connues and chiffre[k + i] in connues], dtype=np.int64)
        P0, vus, meilleur = P.copy(), set(), None
        for rd, rm in itertools.product(range(26), range(26)):
            essai = (0, rm, rd)
            e = tuple((decalages[i] + essai[i]) % 26 for i in range(3))
            for _ in range(k + 1):
                e = e and recule(e, encoches)
            if not e:
                continue
            S2 = Enigma(ordre, "B", essai, e).brouilleurs(len(chiffre))
            clair = P0[S2[rang, P0[chiffre]]]
            if not np.array_equal(clair[k + verif], crib[verif]) or clair.tobytes() in vus:
                continue
            vus.add(clair.tobytes())
            P2, note2 = fiches_par_colline(P0.copy(), S2)
            if meilleur is None or note2 > meilleur[0]:
                meilleur = (note2, essai, e, S2, P2)
        if meilleur:
            note, anneaux, etat, Sm, P = meilleur
    if avec_anneaux:
        while True:
            P2, note2 = deux_coups(P, Sm)
            if note2 <= note + 1e-9:
                break
            P, note = fiches_par_colline(P2, Sm)
    for _ in range(2 if avec_anneaux else 1):
        P, note = fiches_par_colline(P, Sm)
        if not avec_anneaux:
            break
        meilleur = (note, anneaux, etat, Sm)
        for rd, rm in itertools.product(range(26), range(26)):
            essai_anneaux = (0, rm, rd)
            e = tuple((decalages[i] + essai_anneaux[i]) % 26 for i in range(3))
            for _ in range(k + 1):
                e = e and recule(e, encoches)
            if not e:
                continue
            S2 = Enigma(ordre, "B", essai_anneaux, e).brouilleurs(len(chiffre))
            s = evalue(P, S2)
            if s > meilleur[0] + 1e-9:
                meilleur = (s, essai_anneaux, e, S2)
        if meilleur[1] == anneaux:
            break
        note, anneaux, etat, Sm = meilleur
    fiches_finales = [(a, int(P[a])) for a in range(26) if P[a] > a]
    return note, Enigma(ordre, "B", anneaux, etat, fiches_finales), args[0]


def attaque(chiffre_txt, crib_txt, k, langue, rotors=("I", "II", "III", "IV", "V"), processus=None, bavard=True):
    chiffre, crib = nombres(chiffre_txt), nombres(crib_txt)
    aretes = menu(crib, chiffre, k)
    if aretes is None:
        raise ValueError(f"le crib ne peut pas être en position {k} : une lettre y serait chiffrée en elle-même")
    ordres = list(itertools.permutations(rotors, 3))
    if nb_boucles(aretes)[0] == 0:
        raise ValueError("le menu de ce crib n'a aucune boucle : la bombe s'arrêterait presque partout. "
                         "Il faut un crib plus long, ou le placer ailleurs dans le message (--position)")
    if nb_boucles(aretes)[0] < 3 and bavard:
        print("attention : moins de 3 boucles dans le menu, il risque d'y avoir beaucoup d'arrêts à départager")
    if bavard:
        boucles, n_lettres = nb_boucles(aretes)
        print(f"menu : {len(aretes)} arêtes, {n_lettres} lettres, {boucles} boucle(s) autour de la lettre testée ; "
              f"{len(ordres)} ordres de rotors × 17 576 positions × {len(crib)} instants de pas")
    t0 = time.time()
    arrets = []
    with ProcessPoolExecutor(max_workers=processus or os.cpu_count()) as pool:
        for n_fait, res in enumerate(pool.map(bombe_un_ordre, [(o, "B", aretes, len(crib)) for o in ordres]), 1):
            arrets += res
            if bavard:
                print(f"\r  bombe : {n_fait}/{len(ordres)} ordres, {len(arrets)} arrêt(s) vérifié(s)", end="", flush=True)
        if bavard:
            print(f" en {time.time() - t0:.1f} s")
        if len(arrets) >= MAX_ARRETS * len(ordres) // 4:
            raise ValueError(f"{len(arrets)} arrêts : ce menu est trop faible pour départager les positions. "
                             "Il faut un crib plus long (plus de boucles)")
        # finition en deux temps : anneaux pour tous les arrêts (rapide), puis
        # tableau complet seulement pour les 40 réglages les plus prometteurs
        t1 = time.time()
        candidats = [c for liste in pool.map(finition_args, [(a, chiffre, crib, k, langue) for a in arrets], chunksize=16)
                     for c in liste]
        candidats.sort(key=lambda c: -c[0])
        # tableau complet pour les 1500 meilleurs, puis anneaux revus pour les 10 meilleurs de ceux-là
        tableaux = sorted(pool.map(complete_tableau, [(c, chiffre, crib, k, langue, False) for c in candidats[:1500]],
                                   chunksize=8), key=lambda r: -r[0])
        finis = pool.map(complete_tableau, [(r[2], chiffre, crib, k, langue, True) for r in tableaux[:10]])
        resultats = sorted(((note, machine) for note, machine, _ in finis), key=lambda r: -r[0])
    if bavard:
        print(f"  finition (anneaux pour {len(arrets)} arrêts, tableau pour {min(1500, len(candidats))} candidats) : "
              f"{time.time() - t1:.1f} s")
    return resultats


def finition_args(args):
    return finition(*args)


# ------------------------------------------------------------------------ CLI

def lit_reglage(args):
    rotors = tuple(args.rotors.split())
    for r in rotors:
        if r not in ROTORS:
            sys.exit(f"rotor inconnu : {r} (I à V)")
    anneaux = tuple(int(nombres(x)[0]) for x in args.anneaux.split())
    positions = tuple(int(nombres(x)[0]) for x in args.positions.split())
    fiches = [tuple(int(v) for v in nombres(p)) for p in args.fiches.split()]
    vues = [x for p in fiches for x in p]
    if len(rotors) != 3 or len(anneaux) != 3 or len(positions) != 3 or any(len(p) != 2 for p in fiches) or len(vues) != len(set(vues)):
        sys.exit("réglage invalide : 3 rotors, 3 anneaux, 3 positions, des fiches comme « AB CD » sans lettre répétée")
    return Enigma(rotors, args.reflecteur, anneaux, positions, fiches)


def groupes(t, n=5):
    return " ".join(t[i:i + n] for i in range(0, len(t), n))


MESSAGES = [
    "Bulletin météo pour la nuit. Vent de nord-ouest force six, mer agitée, pluie en fin de soirée, "
    "visibilité réduite sur toute la côte. Les patrouilles restent au port jusqu'à nouvel ordre.",
    "Bulletin météo du matin. Ciel dégagé sur le secteur nord, vent faible de sud, mer calme. "
    "Conditions favorables pour la sortie prévue avant l'aube. Confirmer la réception de ce message.",
    "Bulletin météo de la côte ouest. Brouillard épais jusqu'à midi puis éclaircies, vent d'est modéré. "
    "Le convoi attendra la levée du brouillard avant de reprendre la route vers le port.",
]


def main():
    ici = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Enigma et la bombe de Turing-Welchman")
    sous = ap.add_subparsers(dest="cmd", required=True)
    d = sous.add_parser("demo", help="chiffre un message avec un réglage secret au hasard, puis le casse")
    d.add_argument("--graine", type=int, help="pour rejouer la même démo")
    d.add_argument("--rotors-dispo", default="I II III IV V", help="rotors parmi lesquels choisir (défaut : I à V)")
    c = sous.add_parser("chiffrer", help="chiffrer / déchiffrer")
    a = sous.add_parser("attaquer", help="casser un message à partir d'un crib")
    for p in (c,):
        p.add_argument("--rotors", default="I II III", help="ex. « II IV V » (gauche, milieu, droite)")
        p.add_argument("--reflecteur", default="B", choices=REFLECTEURS)
        p.add_argument("--anneaux", default="A A A")
        p.add_argument("--positions", default="A A A")
        p.add_argument("--fiches", default="", help="ex. « AV BS CG DL FU HZ IN KM OW RX »")
        p.add_argument("texte", nargs="+")
    a.add_argument("--crib", required=True, help="texte clair deviné")
    a.add_argument("--position", type=int, default=0, help="place du crib dans le message (défaut 0)")
    a.add_argument("--rotors-dispo", default="I II III IV V")
    a.add_argument("fichier", help="fichier contenant le message chiffré (ou - pour l'entrée standard)")
    args = ap.parse_args()

    langue = Langue(os.path.join(ici, "corpus.txt"))

    if args.cmd == "chiffrer":
        machine = lit_reglage(args)
        print(groupes(machine.chiffre(" ".join(args.texte))))

    elif args.cmd == "attaquer":
        texte = sys.stdin.read() if args.fichier == "-" else open(args.fichier, encoding="utf-8").read()
        possibles = [k for k in range(len(nettoie(texte)) - len(nettoie(args.crib)) + 1)
                     if menu(nombres(args.crib), nombres(texte), k)]
        print(f"positions possibles du crib (aucune lettre chiffrée en elle-même) : {possibles[:20]}"
              f"{' ...' if len(possibles) > 20 else ''}")
        try:
            res = attaque(texte, args.crib, args.position, langue, tuple(args.rotors_dispo.split()))
        except ValueError as e:
            sys.exit(str(e))
        if not res:
            sys.exit("aucun réglage trouvé (essaie une autre --position, ou un crib plus long)")
        note, machine = res[0]
        print(f"\nréglage trouvé : {machine}\n\n{groupes(machine.chiffre(texte))}")

    else:
        rng = random.Random(args.graine)
        dispo = args.rotors_dispo.split()
        crib = "BULLETINMETEO"
        rejetes = 0
        while True:
            rotors = tuple(rng.sample(dispo, 3))
            melange = rng.sample(range(26), 20)
            secret = Enigma(rotors, "B", tuple(rng.randrange(26) for _ in range(3)),
                            tuple(rng.randrange(26) for _ in range(3)), list(zip(melange[::2], melange[1::2])))
            clair = nettoie(rng.choice(MESSAGES))
            chiffre = secret.chiffre(clair)
            # comme à Bletchley Park : on ne lance la bombe que sur un menu qui a des boucles
            if nb_boucles(menu(nombres(crib), nombres(chiffre), 0))[0] >= 3:
                break
            rejetes += 1
        if rejetes:
            print(f"({rejetes} message(s) écarté(s) : leur menu avait moins de 3 boucles)\n")
        print(f"réglage secret : {secret}\n")
        print(f"message chiffré ({len(chiffre)} lettres) :\n{groupes(chiffre)}\n")
        print(f"on suppose que le message commence par « {crib} ».\n")
        t0 = time.time()
        try:
            res = attaque(chiffre, crib, 0, langue, tuple(dispo))
        except ValueError as e:
            print(f"\nabandon : {e}")
            return 1
        if not res:
            print("échec : pas de réglage trouvé (cela arrive si le rotor de gauche bouge pendant le crib)")
            return 1
        note, machine = res[0]
        trouve = machine.chiffre(chiffre)
        justes = sum(x == y for x, y in zip(trouve, clair))
        print(f"\nréglage trouvé : {machine}")
        print(f"\n{groupes(trouve)}\n")
        print(f"{justes}/{len(clair)} lettres justes, {time.time() - t0:.1f} s au total")
        if justes == len(clair) and str(machine) != str(secret):
            print("(réglage équivalent : les anneaux et positions diffèrent mais donnent exactement le même texte)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
