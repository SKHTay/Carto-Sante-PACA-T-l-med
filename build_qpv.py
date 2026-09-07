#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Construit `qpv-zonage.json` à partir de l'arrêté de zonage QPV médecins
de l'ARS PACA (« Arrêté_zonage_QPV_MEDECINS_PACA_2026_02.xlsx », onglet ZD_QPV).

Pourquoi un fichier à part plutôt qu'un champ de plus dans communes-paca.geojson :
l'arrêté QPV est révisé à son propre rythme, indépendamment des contours IGN et
du millésime ARCEP. Le sortir du geojson permet de le remplacer seul, sans
régénérer les 947 contours communaux (plusieurs Mo).

Une ligne = un QPV. La colonne « Commune » peut en lister plusieurs, séparées
par des virgules : un QPV à cheval (Ranguin, entre Cannes et Le Cannet) est
alors rattaché à chacune d'elles. Compter les lignes plutôt que les couples
(QPV, commune) sous-estimerait le nombre de QPV du Cannet.

Usage :
    python3 build_qpv.py "Arrêté_zonage_QPV_MEDECINS_PACA_2026_02.xlsx" \
        qpv-zonage.json [communes-paca.geojson]

Le troisième argument est facultatif : s'il est fourni, le script vérifie que
tous les codes INSEE de l'arrêté existent dans le référentiel de la carte, et
signale ceux qui n'y sont pas (fusion de communes, coquille de saisie).

Dépendances : openpyxl.
"""
import json
import os
import sys
from collections import OrderedDict, defaultdict

ONGLET = "ZD_QPV"
# Libellés de zonage attendus dans la colonne « Zonage 2025 ».
# ZIP = zone d'intervention prioritaire, ZAC = zone d'action complémentaire.
ZONAGES = ("ZIP", "ZAC", "ZAR", "ZV")

COL = {"dep": 0, "insee": 2, "commune": 3, "qpv": 6, "libelle": 7, "zonage": 8}


def lire_arrete(chemin):
    """Retourne (liste de QPV, millésime lu dans le nom de la colonne)."""
    import openpyxl

    wb = openpyxl.load_workbook(chemin, read_only=True, data_only=True)
    if ONGLET not in wb.sheetnames:
        sys.exit("Onglet « %s » introuvable. Onglets présents : %s"
                 % (ONGLET, ", ".join(wb.sheetnames)))
    ws = wb[ONGLET]
    lignes = [r for r in ws.iter_rows(values_only=True)]
    entete = [str(c or "").strip() for c in lignes[0]]

    # L'en-tête porte l'année du zonage (« Zonage 2025 »). On la lit au lieu de
    # la coder en dur : le prochain arrêté changera l'intitulé, pas la structure.
    col_zonage = entete[COL["zonage"]] if len(entete) > COL["zonage"] else ""
    millesime = col_zonage.replace("Zonage", "").strip() or "n. d."

    qpvs, alertes = [], []
    for i, r in enumerate(lignes[1:], start=2):
        if not r or not r[COL["dep"]]:
            continue
        codes = [c.strip() for c in str(r[COL["insee"]]).split(",") if c.strip()]
        noms = [c.strip() for c in str(r[COL["commune"]]).split(",") if c.strip()]
        zonage = str(r[COL["zonage"]] or "").strip().upper()
        if zonage not in ZONAGES:
            alertes.append("  ligne %d : zonage inattendu « %s »" % (i, zonage))
        if not codes:
            alertes.append("  ligne %d : aucun code INSEE" % i)
            continue
        qpvs.append({
            "id": str(r[COL["qpv"]] or "").strip(),
            "nom": str(r[COL["libelle"]] or "").strip(),
            "z": zonage,
            "codes": codes,
            # Le libellé commune n'est conservé que pour les messages d'erreur :
            # le nom affiché sur la carte vient du référentiel IGN.
            "noms": noms,
        })
    return qpvs, millesime, alertes


def agreger(qpvs):
    """QPV → dictionnaire indexé par code INSEE."""
    par_commune = defaultdict(lambda: {"zip": 0, "zac": 0, "qpv": []})
    for q in qpvs:
        for c in q["codes"]:
            e = par_commune[c]
            if q["z"] == "ZIP":
                e["zip"] += 1
            elif q["z"] == "ZAC":
                e["zac"] += 1
            e["qpv"].append({"id": q["id"], "n": q["nom"], "z": q["z"]})
    # Tri : ZIP d'abord (l'information la plus lourde de conséquences), puis
    # ordre alphabétique. L'affichage du popup suit cet ordre tel quel.
    for e in par_commune.values():
        e["qpv"].sort(key=lambda x: (x["z"] != "ZIP", x["n"]))
    return OrderedDict(sorted(par_commune.items()))


def main(xlsx, dst, communes=None):
    qpvs, millesime, alertes = lire_arrete(xlsx)
    par_commune = agreger(qpvs)

    n_zip = sum(1 for q in qpvs if q["z"] == "ZIP")
    n_zac = sum(1 for q in qpvs if q["z"] == "ZAC")
    print("Arrêté lu : %d QPV — %d ZIP, %d ZAC — zonage %s"
          % (len(qpvs), n_zip, n_zac, millesime))
    print("Communes concernées : %d" % len(par_commune))
    a_cheval = [q for q in qpvs if len(q["codes"]) > 1]
    if a_cheval:
        print("QPV à cheval sur plusieurs communes : %d" % len(a_cheval))
        for q in a_cheval:
            print("   %-46s %s  [%s]" % (q["nom"][:46], "/".join(q["noms"]), q["z"]))
    for a in alertes:
        print(a, file=sys.stderr)

    # Rapprochement avec le référentiel de la carte : un code absent signifie
    # un QPV qui ne s'affichera nulle part. Ça ne doit pas passer en silence.
    if communes:
        connus = {f["properties"]["c"]
                  for f in json.load(open(communes, encoding="utf-8"))["features"]}
        orphelins = sorted(set(par_commune) - connus)
        if orphelins:
            print("CODES INSEE SANS CONTOUR (%d) — à traiter, pas à ignorer :"
                  % len(orphelins), file=sys.stderr)
            for c in orphelins:
                noms = {n for q in qpvs if c in q["codes"] for n in q["noms"]}
                print("   %s  %s" % (c, " / ".join(sorted(noms))), file=sys.stderr)
        else:
            print("Rapprochement référentiel : %d/%d codes appariés"
                  % (len(par_commune), len(par_commune)))

    sortie = {
        "millesime": millesime,
        "source": os.path.basename(xlsx),
        "n_qpv": len(qpvs),
        "n_zip": n_zip,
        "n_zac": n_zac,
        "communes": par_commune,
    }
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(sortie, f, ensure_ascii=False, separators=(",", ":"))
    print("%s — %.1f ko" % (dst, os.path.getsize(dst) / 1e3))
    return 1 if alertes else 0


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
