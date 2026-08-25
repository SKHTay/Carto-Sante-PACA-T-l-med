#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Produit `data/arrondissements-marseille.geojson` — les 16 arrondissements
municipaux de Marseille (INSEE 13201 à 13216).

Pourquoi un script dédié : le référentiel communal utilisé par la carte
(france-geojson, branche master) ne descend pas sous la commune. Marseille y est
un seul polygone, 13055. Or huit CPTS se partagent la ville par arrondissement :
sans cette maille, les huit partagent le même contour et la carte ment.

Les arrondissements existent en revanche dans les versions **v2.0.0 à v2.0.2**
du même dépôt, retirés depuis (issue #24). Même source IGN, millésime antérieur.

    https://raw.githubusercontent.com/gregoiredavid/france-geojson/v2.0.2/
        departements/13-bouches-du-rhone/communes-13-bouches-du-rhone.geojson

Deux millésimes ne coïncident pas au trait près : l'union des 16 arrondissements
couvre 97 % de la commune actuelle, avec 2,7 km² qui débordent et 7,1 km² non
couverts. Laissées telles quelles, ces différences se verraient — un contour de
CPTS flottant à côté du littoral. Le script **conforme** donc les arrondissements
à la commune : découpe sur 13055, puis rattachement de chaque résidu à
l'arrondissement le plus proche. Les 16 pavent alors exactement la commune.

Usage :
    python3 tools/build_arrondissements_marseille.py source-v2.0.2.geojson \\
        data/communes-paca.geojson data/arrondissements-marseille.geojson

Dépendances : shapely, pyproj.
"""
import json, os, re, sys

PREC = 4
CODE_MARSEILLE = "13055"


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


def main(src_path, communes_path, dst_path):
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union, transform
    from pyproj import Transformer
    vers_l93 = Transformer.from_crs(4326, 2154, always_xy=True).transform
    km2 = lambda g: transform(vers_l93, g).area / 1e6

    src = json.load(open(src_path, encoding="utf-8"))
    arr = [f for f in src["features"]
           if re.match(r"^132(0[1-9]|1[0-6])$", str(f["properties"].get("code", "")))]
    arr.sort(key=lambda f: f["properties"]["code"])
    if len(arr) != 16:
        sys.exit("Attendu 16 arrondissements, trouvé %d — mauvaise source ?" % len(arr))

    communes = json.load(open(communes_path, encoding="utf-8"))["features"]
    mars = [f for f in communes if f["properties"]["c"] == CODE_MARSEILLE]
    if not mars:
        sys.exit("Commune %s absente du référentiel." % CODE_MARSEILLE)
    ville = shape(mars[0]["geometry"]).buffer(0)
    print("Commune %s : %.2f km²" % (CODE_MARSEILLE, km2(ville)))

    brut = [shape(f["geometry"]).buffer(0) for f in arr]
    print("Union des 16 arrondissements (millésime source) : %.2f km²"
          % km2(unary_union(brut)))

    # 1. découpe sur la commune actuelle
    coupes = [g.intersection(ville).buffer(0) for g in brut]
    deborde = km2(unary_union(brut).difference(ville))

    # 2. les résidus de la commune non couverts rejoignent l'arrondissement le
    #    plus proche, pour que les 16 pavent exactement 13055
    residu = ville.difference(unary_union(coupes)).buffer(0)
    n_res, surf_res = 0, km2(residu) if not residu.is_empty else 0.0
    if not residu.is_empty:
        for morceau in getattr(residu, "geoms", [residu]):
            if morceau.is_empty:
                continue
            n_res += 1
            i = min(range(16), key=lambda k: coupes[k].distance(morceau))
            coupes[i] = unary_union([coupes[i], morceau]).buffer(0)
    print("Conformation : %.2f km² écartés (hors commune), "
          "%.2f km² rattachés (%d résidus)" % (deborde, surf_res, n_res))

    # 3. contrôles
    total = unary_union(coupes)
    ecart = km2(ville.symmetric_difference(total))
    chevauchement = 0.0
    for i in range(16):
        for j in range(i + 1, 16):
            if coupes[i].intersects(coupes[j]):
                chevauchement += km2(coupes[i].intersection(coupes[j]))
    print("Contrôle — écart au contour communal : %.4f km² | "
          "chevauchement entre arrondissements : %.4f km²" % (ecart, chevauchement))
    if ecart > 0.05 or chevauchement > 0.05:
        print("ATTENTION : le pavage n'est pas propre, ne pas publier tel quel",
              file=sys.stderr)

    feats = []
    for f, g in zip(arr, coupes):
        if g.is_empty:
            print("  %s vide après découpe" % f["properties"]["code"], file=sys.stderr)
            continue
        geom = arrondi_geom(mapping(g))
        if geom is None:
            continue
        # 'Marseille 8e  Arrondissement' (double espace dans la source)
        nom = re.sub(r"\s+", " ", f["properties"]["nom"]).strip()
        feats.append({"type": "Feature",
                      "properties": {"c": f["properties"]["code"], "n": nom,
                                     "commune": CODE_MARSEILLE,
                                     "km2": round(km2(g), 2)},
                      "geometry": geom})

    json.dump({"type": "FeatureCollection", "features": feats},
              open(dst_path, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("%s — %d arrondissements, %.0f Ko"
          % (dst_path, len(feats), os.path.getsize(dst_path) / 1e3))
    for f in feats:
        print("   %s  %-32s %6.2f km²"
              % (f["properties"]["c"], f["properties"]["n"], f["properties"]["km2"]))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
