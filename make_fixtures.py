#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jeux de données factices pour tester index.html sans les vrais geojson.

Géométries = petits carrés disjoints, un par commune, posés sur une grille par
département. Aucune valeur cartographique : le but est uniquement de vérifier
le comportement de la page (compteurs, filtres, menu CPTS, popups, export).
"""
import json
import os
import sys

DEPTS = ["04", "05", "06", "13", "83", "84"]
ORIGINE = {"04": (6.2, 44.1), "05": (6.3, 44.6), "06": (7.2, 43.8),
           "13": (5.2, 43.5), "83": (6.2, 43.3), "84": (5.1, 44.0)}


def carre(lon, lat, t=0.03):
    return {"type": "Polygon", "coordinates": [[
        [round(lon, 4), round(lat, 4)], [round(lon + t, 4), round(lat, 4)],
        [round(lon + t, 4), round(lat + t, 4)], [round(lon, 4), round(lat + t, 4)],
        [round(lon, 4), round(lat, 4)]]]}


def main(qpv_path, out):
    os.makedirs(out, exist_ok=True)
    qpv = json.load(open(qpv_path, encoding="utf-8"))

    # Toutes les communes de l'arrêté + du remplissage pour atteindre un volume
    # réaliste et fournir des communes hors ZIP et sans QPV.
    codes = sorted(qpv["communes"])
    for d in DEPTS:
        for i in range(20):
            codes.append("%s9%02d" % (d, i))
    codes = sorted(set(codes))

    feats, pos = [], {d: 0 for d in DEPTS}
    for c in codes:
        d = c[:2]
        if d not in DEPTS:
            continue
        i = pos[d]
        pos[d] += 1
        lon0, lat0 = ORIGINE[d]
        lon = lon0 + (i % 10) * 0.05
        lat = lat0 + (i // 10) * 0.05
        # z (commune ZIP) et b (zone blanche) : valeurs arbitraires mais
        # déterministes, pour que les compteurs soient prévisibles.
        z = 1 if (int(c[2:]) % 3) else 0
        b = 1 if (int(c[2:]) % 4 == 0) else 0
        feats.append({"type": "Feature", "geometry": carre(lon, lat),
                      "properties": {"c": c, "n": "Commune " + c, "d": d,
                                     "z": z, "q": 0, "b": b}})
    json.dump({"type": "FeatureCollection", "features": feats},
              open(os.path.join(out, "communes-paca.geojson"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    print("communes-paca.geojson : %d communes (%d ZIP, %d zone blanche)"
          % (len(feats), sum(f["properties"]["z"] for f in feats),
             sum(f["properties"]["b"] for f in feats)))

    # Zones blanches : un gros carré par département.
    zb = [{"type": "Feature", "properties": {"d": d, "km2": 300.0 + 10 * i, "n": 12},
           "geometry": carre(ORIGINE[d][0] - 0.2, ORIGINE[d][1] - 0.2, 0.15)}
          for i, d in enumerate(DEPTS)]
    json.dump({"type": "FeatureCollection", "features": zb},
              open(os.path.join(out, "zones-blanches-4g.geojson"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    print("zones-blanches-4g.geojson : %d polygones" % len(zb))

    # 16 arrondissements marseillais, carrés alignés.
    arr = [{"type": "Feature",
            "properties": {"c": "132%02d" % k, "n": "Marseille %d%s Arrondissement"
                           % (k, "er" if k == 1 else "e"), "km2": 5.0 + k},
            "geometry": carre(5.30 + (k % 4) * 0.02, 43.28 + (k // 4) * 0.02, 0.018)}
           for k in range(1, 17)]
    json.dump({"type": "FeatureCollection", "features": arr},
              open(os.path.join(out, "arrondissements-marseille.geojson"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    print("arrondissements-marseille.geojson : %d" % len(arr))

    # Contours QPV : un petit carré posé dans la commune de rattachement, avec
    # le zonage repris du fichier d'arrêté. Reproduit le cas des QPV à cheval
    # (plusieurs communes dans `com`) qui pilote le filtre par périmètre CPTS.
    par_qpv = {}
    for code, e in qpv["communes"].items():
        for q in e["qpv"]:
            par_qpv.setdefault(q["id"], {"n": q["n"], "z": q["z"], "com": []})
            par_qpv[q["id"]]["com"].append(code)
    geom_com = {f["properties"]["c"]: f["geometry"] for f in feats}
    qf, k = [], {}
    for qid, e in sorted(par_qpv.items()):
        c0 = sorted(e["com"])[0]
        if c0 not in geom_com:
            continue
        base = geom_com[c0]["coordinates"][0][0]
        i = k[c0] = k.get(c0, 0) + 1
        qf.append({"type": "Feature",
                   "properties": {"q": qid, "n": e["n"], "z": e["z"],
                                  "d": c0[:2], "com": sorted(e["com"])},
                   "geometry": carre(base[0] + 0.002 * (i % 5),
                                     base[1] + 0.002 * (i // 5), 0.0015)})
    json.dump({"type": "FeatureCollection", "millesime": qpv["millesime"],
               "features": qf},
              open(os.path.join(out, "qpv-paca.geojson"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    print("qpv-paca.geojson : %d contours QPV" % len(qf))

    # CPTS : une par département sur des communes réelles de l'arrêté, plus
    # trois CPTS marseillaises qui se partagent les arrondissements (le cas
    # qui casse le plus facilement), et une CPTS sans contact ni site.
    par_dep = {}
    for c in sorted(qpv["communes"]):
        par_dep.setdefault(c[:2], []).append(c)
    cpts = []
    for d in DEPTS:
        cs = par_dep.get(d, [])[:3]
        if not cs:
            continue
        cpts.append({"nom": "CPTS Territoire %s" % d, "communes": cs,
                     "statut": "signataire",
                     "president": "Dr Exemple %s" % d,
                     "coordonnateur": "Coord %s" % d,
                     "contact": "cpts%s@example.org" % d,
                     "site": "www.cpts%s.example.org" % d})
    for k, (nom, arrs) in enumerate([
            ("CPTS Marseille Nord", ["13213", "13214", "13215", "13216"]),
            ("CPTS Marseille Centre", ["13202", "13203", "13206"]),
            ("CPTS Marseille Sud", ["13207", "13208", "13209"])]):
        cpts.append({"nom": nom, "communes": ["13055"], "arrondissements": arrs,
                     "statut": "valide" if k else "signataire",
                     "president": "Dr Marseille %d" % k,
                     "coordonnateur": "", "contact": "", "site": ""})
    cpts.append({"nom": "CPTS Sans Contact", "communes": par_dep["84"][3:6],
                 "statut": "", "president": "", "coordonnateur": "",
                 "contact": "", "site": "", "infra": ["Avignon"]})

    geom = {f["properties"]["c"]: f["geometry"] for f in feats}
    geom.update({f["properties"]["c"]: f["geometry"] for f in arr})
    cf = []
    for p in cpts:
        parts = [geom[a] for a in p.get("arrondissements", [])] or \
                [geom[c] for c in p["communes"] if c in geom]
        if not parts:
            continue
        cf.append({"type": "Feature", "properties": p,
                   "geometry": {"type": "MultiPolygon",
                                "coordinates": [g["coordinates"] for g in parts]}})
    json.dump({"type": "FeatureCollection", "features": cf},
              open(os.path.join(out, "cpts.geojson"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    print("cpts.geojson : %d CPTS" % len(cf))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
