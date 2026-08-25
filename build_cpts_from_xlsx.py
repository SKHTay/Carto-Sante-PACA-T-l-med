#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Construit `data/cpts.geojson` à partir du listing Excel des CPTS de l'ARS PACA.

Le listing donne une ligne par couple (CPTS, commune). Les périmètres sont donc
reconstruits par agrégation des contours communaux — il n'y a pas de géométrie
dans le fichier source.

Marseille est traitée à l'arrondissement : le listing y nomme « Marseille 14e
Arrondissement », et huit CPTS se partagent la ville. Passer le fichier des
arrondissements en quatrième argument (voir build_arrondissements_marseille.py)
donne à ces huit CPTS leur vrai périmètre au lieu du contour de la ville entière.

Usage :
    python3 tools/build_cpts_from_xlsx.py "Listing CPTS_012026.xlsx" \\
        data/communes-paca.geojson data/cpts.geojson \\
        [data/arrondissements-marseille.geojson]

Colonnes attendues (ligne 2 du fichier ARS) :
    Nom CPTS | Territoire CPTS | dpt | Président-e | Coordonnateur-rice
             | Contact de la CPTS | Site Internet

Dépendances : openpyxl, shapely.
"""
import json, os, re, sys, unicodedata
from collections import defaultdict

PREC = 4   # décimales conservées (~11 m), comme build_data.py

# Communes fusionnées depuis la rédaction du listing : l'ARS nomme encore les
# communes déléguées, le référentiel géographique ne connaît que la commune
# nouvelle. Sans cette table, ces lignes seraient silencieusement perdues.
FUSIONS = {
    ("bruis", "05"): "Valdoule",
    ("montmorin", "05"): "Valdoule",
    ("saintemarie", "05"): "Valdoule",
    ("chauffayer", "05"): "Aubessagne",
    ("costes", "05"): "Aubessagne",
    ("sainteusebeenchampsaur", "05"): "Aubessagne",
}

# « Marseille 14e Arrondissement » → ville + numéro
ARRONDISSEMENT = re.compile(
    r"^(marseille|nice|toulon|avignon|aix[\s-]*en[\s-]*provence)\s*"
    r"(\d+)\s*(?:er|e|eme|ème)?\s*arrondissement", re.I)
# Seule Marseille dispose d'une maille infra-communale dans le référentiel
# (arrondissements municipaux 13201-13216). Nice et Toulon n'en ont pas — et le
# listing ARS ne les subdivise pas non plus : il y écrit simplement « Nice ».
PREFIXE_ARRONDISSEMENT = {"marseille": "132"}


def sans_accent(s):
    s = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def cle(s):
    s = sans_accent(s)
    s = re.sub(r"[’']", " ", s)
    s = re.sub(r"\bst\b", "saint", s)
    s = re.sub(r"\bste\b", "sainte", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def variantes(nom):
    """Clés de rapprochement d'un nom de commune.

    Deux écarts systématiques entre le listing ARS et le référentiel
    géographique sont traités ici :
      - l'article. Le référentiel INSEE stocke le libellé SANS article
        ('Garde', 'Escale', 'Tholonet') là où l'ARS écrit 'La Garde',
        'L'Escale', 'Le Tholonet'. C'est la cause de 130 des 923 lignes.
      - la cédille. Le fichier ARS contient 'Ponèon' et 'Argenèon' :
        un ç mal transcodé. On tente donc aussi la variante è → ç.
    """
    n = re.sub(r"[’']", "'", str(nom)).strip()
    v = {n}
    m = re.match(r"^(Les|Le|La|L)['\s]+(.+)$", n, re.I)
    if m:
        v.add(m.group(2))
    for x in list(v):
        if "è" in x or "è" in sans_accent(x):
            v.add(x.replace("è", "ç"))
    return {cle(x) for x in v}


def arrondi_ring(ring):
    out = []
    for x, y in ring:
        q = (round(x, PREC), round(y, PREC))
        if not out or out[-1] != q:
            out.append(q)
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])
    return [list(p) for p in out] if len(out) >= 4 else None


def arrondi_geom(g):
    if g["type"] == "Polygon":
        rs = [r for r in (arrondi_ring(x) for x in g["coordinates"]) if r]
        return {"type": "Polygon", "coordinates": rs} if rs else None
    ps = []
    for poly in g["coordinates"]:
        rs = [r for r in (arrondi_ring(x) for x in poly) if r]
        if rs:
            ps.append(rs)
    return {"type": "MultiPolygon", "coordinates": ps} if ps else None


def main(xlsx_path, communes_path, dst_path, arr_path=None):
    import openpyxl
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union

    communes = json.load(open(communes_path, encoding="utf-8"))["features"]
    index = defaultdict(set)
    geom_par_code, nom_par_code = {}, {}
    for f in communes:
        p = f["properties"]
        geom_par_code[p["c"]] = shape(f["geometry"]).buffer(0)
        nom_par_code[p["c"]] = p["n"]
        for k in variantes(p["n"]):
            index[(k, p["d"])].add(p["c"])
    print("Référentiel : %d communes PACA" % len(communes))

    geom_arr, nom_arr = {}, {}
    if arr_path:
        for f in json.load(open(arr_path, encoding="utf-8"))["features"]:
            p = f["properties"]
            geom_arr[p["c"]] = shape(f["geometry"]).buffer(0)
            nom_arr[p["c"]] = p["n"]
        print("Maille infra-communale : %d arrondissements" % len(geom_arr))
    else:
        print("Aucun fichier d'arrondissements : Marseille restera un seul "
              "contour partagé par ses CPTS.")

    ws = openpyxl.load_workbook(xlsx_path, read_only=True).active
    lignes = [r for r in ws.iter_rows(values_only=True)][2:]
    lignes = [r for r in lignes if r and r[0]]
    print("Listing ARS : %d lignes (CPTS × commune)" % len(lignes))

    membres = defaultdict(set)       # communes dont on prend le contour entier
    porteuses = defaultdict(set)     # communes représentées par leurs arrondissements
    membres_arr = defaultdict(set)
    infos, non_apparies, infra, arr_manquants = {}, [], defaultdict(set), []
    for r in lignes:
        nom = str(r[0]).strip()
        territoire = str(r[1]).strip()
        dep = str(r[2]).strip().zfill(2)
        m = ARRONDISSEMENT.match(territoire)
        cible = m.group(1) if m else territoire
        if m:
            ville = sans_accent(m.group(1)).replace(" ", "").replace("-", "")
            prefixe = PREFIXE_ARRONDISSEMENT.get(ville)
            if prefixe:
                code_arr = prefixe + "%02d" % int(m.group(2))
                if code_arr in geom_arr:
                    membres_arr[nom].add(code_arr)
                else:
                    arr_manquants.append((nom, territoire))
                    infra[nom].add(m.group(1).title())
            else:
                # ville découpée dans le listing mais sans maille disponible
                infra[nom].add(m.group(1).title())
        cles = variantes(cible)
        fusion = FUSIONS.get((cle(re.sub(r"^(Les|Le|La|L)['\s]+", "", cible, flags=re.I)), dep))
        if fusion:
            cles |= variantes(fusion)
        codes = set()
        for k in cles:
            codes |= index.get((k, dep), set())
        if not codes:
            non_apparies.append((nom, territoire, dep))
            continue
        # Ligne résolue à l'arrondissement : la commune entière ne doit PAS
        # entrer dans la géométrie, sinon les huit CPTS marseillaises couvrent
        # de nouveau toute la ville. Elle reste citée comme commune porteuse.
        if m and nom in membres_arr and territoire.lower().startswith(
                sans_accent(m.group(1))[:6]):
            porteuses[nom] |= codes
        else:
            membres[nom] |= codes
        infos.setdefault(nom, {
            "president": str(r[3] or "").strip(),
            "coordonnateur": str(r[4] or "").strip(),
            "contact": str(r[5] or "").strip(),
            "site": str(r[6] or "").strip(),
        })

    print("Lignes appariées : %d / %d" % (len(lignes) - len(non_apparies), len(lignes)))
    if non_apparies:
        print("NON APPARIÉES (%d) — à traiter, pas à ignorer :" % len(non_apparies))
        for n, t, d in non_apparies:
            print("   %-40s %s   [%s]" % (t, d, n))

    feats = []
    for nom in sorted(set(membres) | set(membres_arr) | set(porteuses)):
        codes = sorted(membres[nom])
        codes_arr = sorted(membres_arr[nom])
        codes_tous = sorted(set(codes) | porteuses[nom])
        # Le périmètre dessiné suit l'arrondissement quand on l'a, la commune
        # sinon. La liste `communes` garde la commune porteuse (13055) pour que
        # la fiche de Marseille continue de citer ses CPTS.
        morceaux = [geom_par_code[c] for c in codes] + [geom_arr[a] for a in codes_arr]
        if not morceaux:
            continue
        g = unary_union(morceaux).buffer(0)
        geom = arrondi_geom(mapping(g))
        if geom is None:
            print("  géométrie vide : %s" % nom)
            continue
        info = dict(infos.get(nom, {}))
        info["site"] = "" if info.get("site") in ("0", "None") else info.get("site", "")
        props = {"nom": nom, "statut": "", "communes": codes_tous}
        if codes_arr:
            props["arrondissements"] = codes_arr
        props.update(info)
        if nom in infra:
            props["infra"] = sorted(infra[nom])
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    couvertes = {c for f in feats for c in f["properties"]["communes"]}
    partagees, partagees_arr = defaultdict(list), defaultdict(list)
    for f in feats:
        for c in f["properties"]["communes"]:
            partagees[c].append(f["properties"]["nom"])
        for a in f["properties"].get("arrondissements", []):
            partagees_arr[a].append(f["properties"]["nom"])

    print("\nCPTS reconstruites : %d" % len(feats))
    print("Communes couvertes : %d / %d (%d sans CPTS)"
          % (len(couvertes), len(communes), len(communes) - len(couvertes)))

    if geom_arr:
        arr_couverts = set(partagees_arr)
        print("Arrondissements couverts : %d / %d" % (len(arr_couverts), len(geom_arr)))
        for a in sorted(set(geom_arr) - arr_couverts):
            print("   %-32s aucune CPTS dans le listing" % nom_arr[a])
        doubles = {a: v for a, v in partagees_arr.items() if len(v) > 1}
        for a, v in sorted(doubles.items()):
            print("   %-32s %d CPTS se le partagent" % (nom_arr[a], len(v)))
    if arr_manquants:
        print("Arrondissements nommés dans le listing mais absents du "
              "référentiel : %d" % len(arr_manquants))
        for n, t in arr_manquants:
            print("   %-40s [%s]" % (t, n))

    # Communes encore partagées : villes que le listing ne subdivise pas et qui
    # n'ont pas d'arrondissement municipal. L'avertissement se déduit du
    # résultat, pas du libellé — « Nice » ne contient pas le mot arrondissement,
    # et se fier au libellé laissait ces CPTS sans mention.
    multi = {c: v for c, v in partagees.items()
             if len(v) > 1 and not any(
                 f["properties"].get("arrondissements")
                 for f in feats if f["properties"]["nom"] in v)}
    reste = {c: v for c, v in partagees.items() if len(v) > 1 and c not in multi}
    for c, noms in multi.items():
        for f in feats:
            if f["properties"]["nom"] in noms:
                villes = set(f["properties"].get("infra", [])) | {nom_par_code[c]}
                f["properties"]["infra"] = sorted(villes)
    if multi:
        print("Communes partagées sans maille infra-communale : %d" % len(multi))
        for c, v in sorted(multi.items()):
            print("   %-14s %d CPTS — contour commun, périmètres réels inconnus"
                  % (nom_par_code[c], len(v)))
    for c, v in sorted(reste.items()):
        print("   %-14s %d CPTS — découpé par arrondissement (contours distincts)"
              % (nom_par_code[c], len(v)))

    json.dump({"type": "FeatureCollection", "features": feats},
              open(dst_path, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("%s — %.2f Mo" % (dst_path, os.path.getsize(dst_path) / 1e6))
    return 1 if non_apparies else 0


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
