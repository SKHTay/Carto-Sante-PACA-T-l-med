#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convertit un export CPTS national en la couche `data/cpts.geojson` de la carte.

Source visée : la couche polygonale « CPTS et projets de CPTS » de Santégraphie
(ex-AtlaSanté), producteur DGOS. À télécharger dans un navigateur — ni ce script
ni la carte ne sortent sur le réseau :

  https://santegraphie.fr/geoserver/GCS/ows?service=WFS&version=1.0.0
    &request=GetFeature&typeName=GCS%3AAMBU_CPTS&outputFormat=application%2Fjson

Usage :
    python3 tools/convert_cpts.py cpts_national.geojson data/communes-paca.geojson data/cpts.geojson

Le script ne suppose PAS de connaître les noms de champs de la source : il les
détecte. C'est volontaire — le schéma d'une couche AtlaSanté change d'un
millésime à l'autre, et un script qui code en dur `nom_cpts` casse au premier
export suivant. Ce qui est détecté est affiché à l'écran : vérifiez-le.

Dépendances : shapely, pyproj (pyproj seulement si la source n'est pas en WGS84).
"""
import json, re, sys, unicodedata

# Champs candidats, par ordre de préférence, en minuscules sans accents.
CHAMPS_NOM = ["nom_cpts", "nom", "libelle", "lib_cpts", "nom_struct",
              "raison_sociale", "denomination", "intitule", "label", "name"]
CHAMPS_STATUT = ["statut", "etat", "avancement", "etat_avancement", "stade",
                 "niveau", "categorie", "type_statut", "etape"]

# Vocabulaire ARS → les 4 valeurs attendues par la carte.
# Testées dans cet ordre, en mots entiers. Le mot entier n'est pas un détail :
# en sous-chaîne, "acti" (de l'ACI) se trouve à l'intérieur de "rédACTIon" et
# range une CPTS en cours de rédaction parmi les signataires.
REGLES_STATUT = [
    ("signataire",  ["signataire", "signataires", "signe", "signee", "signature",
                     "contrat", "contractualise", "aci", "acti"]),
    ("redaction",   ["redaction", "elaboration", "emergence", "construction",
                     "cours", "instruction", "depose"]),
    ("valide",      ["valide", "validee", "validation", "approuve", "approuve"]),
    ("initiative",  ["initiative", "reperee", "repere", "projet", "intention",
                     "amorce", "emergent"]),
]


def sans_accent(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s))
                   if unicodedata.category(c) != "Mn").lower().strip()


def choisir_champ(props, candidats):
    cles = {sans_accent(k): k for k in props}
    for c in candidats:
        if c in cles:
            return cles[c]
    for c in candidats:                      # correspondance partielle
        for norm, brut in cles.items():
            if c in norm:
                return brut
    return None


def normaliser_statut(valeur):
    mots_valeur = set(re.findall(r"[a-z0-9]+", sans_accent(valeur)))
    for cible, mots in REGLES_STATUT:
        if mots_valeur & set(mots):
            return cible
    return None


def main(src_path, communes_path, dst_path):
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union, transform
    from shapely.strtree import STRtree

    src = json.load(open(src_path, encoding="utf-8"))
    feats_src = src.get("features", [])
    if not feats_src:
        sys.exit("Source vide : aucune entité dans %s" % src_path)
    print("Entités dans la source : %d" % len(feats_src))

    # --- CRS : WGS84 attendu ; sinon on reprojette d'après le membre crs ou
    #     d'après l'ordre de grandeur des coordonnées (Mercator / Lambert-93).
    def premiere_coord(g):
        c = g["coordinates"]
        while isinstance(c[0], (list, tuple)):
            c = c[0]
        return c
    x0, y0 = premiere_coord(feats_src[0]["geometry"])[:2]
    epsg = None
    nom_crs = json.dumps(src.get("crs", "")) if "crs" in src else ""
    for code in ("4326", "3857", "2154"):
        if code in nom_crs:
            epsg = int(code)
    if epsg is None:
        epsg = 4326 if abs(x0) <= 180 and abs(y0) <= 90 else (
            2154 if x0 > 100000 and y0 > 1000000 and x0 < 1300000 else 3857)
    print("CRS source détecté : EPSG:%d" % epsg)
    reproj = None
    if epsg != 4326:
        from pyproj import Transformer
        reproj = Transformer.from_crs(epsg, 4326, always_xy=True).transform

    # --- champs
    props0 = feats_src[0].get("properties", {}) or {}
    champ_nom = choisir_champ(props0, CHAMPS_NOM)
    champ_statut = choisir_champ(props0, CHAMPS_STATUT)
    print("Champs disponibles : %s" % ", ".join(sorted(props0)))
    print("→ nom    : %s" % (champ_nom or "AUCUN — vérifiez la source"))
    print("→ statut : %s" % (champ_statut or "aucun (statut laissé vide)"))
    if champ_nom is None:
        sys.exit("Impossible d'identifier le champ du nom. Ajoutez-le à "
                 "CHAMPS_NOM en tête de ce script et relancez.")

    # --- emprise PACA, depuis les contours déjà présents dans le dépôt
    communes = json.load(open(communes_path, encoding="utf-8"))["features"]
    geoms_com = [shape(f["geometry"]).buffer(0) for f in communes]
    codes_com = [f["properties"]["c"] for f in communes]
    paca = unary_union(geoms_com)
    arbre = STRtree(geoms_com)
    print("Emprise PACA : %d communes" % len(communes))

    # --- filtrage + rattachement des communes
    sortie, hors_paca, sans_statut = [], 0, 0
    for f in feats_src:
        g = shape(f["geometry"])
        if reproj:
            g = transform(reproj, g)
        g = g.buffer(0)
        if g.is_empty or not g.intersects(paca):
            hors_paca += 1
            continue
        p = f.get("properties", {}) or {}
        statut = normaliser_statut(p.get(champ_statut, "")) if champ_statut else None
        if statut is None:
            sans_statut += 1
        # Une commune est rattachée si son centre tombe dans la CPTS : le simple
        # contact de frontières rattacherait toutes les voisines.
        rattachees = sorted(codes_com[i] for i in arbre.query(g)
                            if g.contains(geoms_com[i].representative_point()))
        sortie.append({
            "type": "Feature",
            "properties": {"nom": str(p.get(champ_nom, "")).strip() or "CPTS",
                           "statut": statut or "",
                           "communes": rattachees},
            "geometry": mapping(g),
        })

    print("CPTS retenues (intersectant PACA) : %d | écartées hors région : %d"
          % (len(sortie), hors_paca))
    if sans_statut:
        print("  dont %d sans statut reconnu — la carte les affichera « n. d. »"
              % sans_statut)
    couvertes = {c for f in sortie for c in f["properties"]["communes"]}
    print("Communes rattachées à une CPTS : %d / %d"
          % (len(couvertes), len(communes)))
    orphelines = len(communes) - len(couvertes)
    if orphelines:
        print("  %d communes PACA ne sont rattachées à aucune CPTS — plausible "
              "(le maillage n'est pas complet), à vérifier si le nombre surprend."
              % orphelines)

    json.dump({"type": "FeatureCollection", "features": sortie},
              open(dst_path, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    import os
    print("%s — %.2f Mo" % (dst_path, os.path.getsize(dst_path) / 1e6))
    if os.path.getsize(dst_path) > 3e6:
        print("  Fichier lourd : envisagez d'arrondir les coordonnées "
              "(voir arrondi_geom dans build_data.py).")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
