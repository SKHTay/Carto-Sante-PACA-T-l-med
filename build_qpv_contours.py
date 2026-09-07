#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Construit `qpv-paca.geojson` : les contours des 135 QPV de PACA, portant le
zonage ZIP/ZAC de l'arrêté ARS.

Source : export national des quartiers prioritaires 2024 (ANCT / data.gouv.fr),
fichier WGS84 — `QP2024_France_Hexagonale_Outre_Mer_WGS84.geojson`. Prendre la
version WGS84 évite toute reprojection : Leaflet attend des longitudes et
latitudes, le fichier LB93 imposerait une conversion Lambert-93 inutile ici.

Deux traitements pour que le fichier reste chargeable dans un navigateur :
  - filtrage sur la région PACA (insee_reg 93), soit 135 des 1 584 entités ;
  - simplification Douglas-Peucker puis arrondi des coordonnées.

Le zonage (ZIP ou ZAC) ne figure PAS dans l'export ANCT : il vient de l'arrêté
ARS via qpv-zonage.json. Le script exige que les deux jeux se recouvrent
exactement et s'arrête sinon — un QPV sans zonage s'afficherait en gris muet,
un zonage sans contour disparaîtrait de la carte.

Usage :
    python3 build_qpv_contours.py QP2024_France_Hexagonale_Outre_Mer_WGS84.geojson \\
        qpv-zonage.json qpv-paca.geojson

Aucune dépendance : bibliothèque standard uniquement.
"""
import json
import math
import os
import sys

REGION = "93"          # Provence-Alpes-Côte d'Azur
TOLERANCE = 1.5e-5     # ~1,7 m. Les QPV sont de petits polygones urbains :
                       # au-delà, les contours perdent leur forme reconnaissable.
DECIMALES = 5          # ~1,1 m — inutile de stocker plus fin que la tolérance.


def perp(p, a, b):
    """Distance du point p au segment [a, b], en degrés."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def rdp(pts, tol):
    """Douglas-Peucker itératif : la récursion déborde sur les gros anneaux."""
    if len(pts) < 3:
        return pts[:]
    garder = [False] * len(pts)
    garder[0] = garder[-1] = True
    pile = [(0, len(pts) - 1)]
    while pile:
        i, j = pile.pop()
        dmax, imax = 0.0, i
        for k in range(i + 1, j):
            d = perp(pts[k], pts[i], pts[j])
            if d > dmax:
                dmax, imax = d, k
        if dmax > tol:
            garder[imax] = True
            pile.append((i, imax))
            pile.append((imax, j))
    return [p for p, g in zip(pts, garder) if g]


def anneau(coords, tol):
    """Simplifie un anneau fermé sans le laisser dégénérer."""
    ferme = coords[0] == coords[-1]
    pts = coords[:-1] if ferme else coords[:]
    out = rdp(pts + [pts[0]], tol)[:-1] if ferme else rdp(pts, tol)
    # Un anneau de moins de trois sommets n'est plus une surface : on rend
    # l'original plutôt que de produire une géométrie invalide.
    if len(out) < 3:
        out = pts
    out = [[round(x, DECIMALES), round(y, DECIMALES)] for x, y in out]
    if ferme:
        out.append(out[0])
    return out


def simplifier(geom, tol):
    t = geom["type"]
    if t == "Polygon":
        return {"type": t, "coordinates": [anneau(r, tol) for r in geom["coordinates"]]}
    if t == "MultiPolygon":
        return {"type": t, "coordinates":
                [[anneau(r, tol) for r in poly] for poly in geom["coordinates"]]}
    raise ValueError("géométrie inattendue : " + t)


def sommets(geom):
    c = geom["coordinates"]
    if geom["type"] == "Polygon":
        return sum(len(r) for r in c)
    return sum(len(r) for poly in c for r in poly)


def main(src, zonage_path, dst):
    zonage = json.load(open(zonage_path, encoding="utf-8"))
    # Un QPV peut relever de plusieurs communes : on reconstitue la liste des
    # libellés depuis l'arrêté, l'export ANCT n'en portant qu'une seule.
    z_par_code, communes_par_code = {}, {}
    for code_insee, e in zonage["communes"].items():
        for q in e["qpv"]:
            z_par_code[q["id"]] = q["z"]
            communes_par_code.setdefault(q["id"], []).append(code_insee)

    src_data = json.load(open(src, encoding="utf-8"))
    feats = [f for f in src_data["features"]
             if f["properties"].get("insee_reg") == REGION]
    print("Entités lues : %d au total, %d en PACA" % (len(src_data["features"]), len(feats)))

    codes_contours = {f["properties"]["code_qp"] for f in feats}
    codes_arrete = set(z_par_code)
    manquants = sorted(codes_arrete - codes_contours)
    surplus = sorted(codes_contours - codes_arrete)
    if manquants or surplus:
        if manquants:
            print("QPV de l'arrêté sans contour : %s" % ", ".join(manquants), file=sys.stderr)
        if surplus:
            print("Contours sans zonage : %s" % ", ".join(surplus), file=sys.stderr)
        sys.exit("Les deux jeux ne se recouvrent pas : corriger avant de publier.")
    print("Rapprochement : %d QPV appariés, aucun orphelin" % len(codes_arrete))

    avant = apres = 0
    sortie = []
    for f in sorted(feats, key=lambda x: x["properties"]["code_qp"]):
        p = f["properties"]
        code = p["code_qp"]
        avant += sommets(f["geometry"])
        geom = simplifier(f["geometry"], TOLERANCE)
        apres += sommets(geom)
        sortie.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "q": code,
                "n": p.get("lib_qp", ""),
                "z": z_par_code[code],
                "d": p.get("insee_dep", ""),
                # Communes de rattachement selon l'arrêté, pas selon l'ANCT.
                "com": sorted(communes_par_code.get(code, [])),
            },
        })

    nz = sum(1 for f in sortie if f["properties"]["z"] == "ZIP")
    print("Simplification : %d → %d sommets (%.0f %% retirés)"
          % (avant, apres, 100 * (1 - apres / avant)))
    print("Sortie : %d QPV — %d ZIP, %d ZAC" % (len(sortie), nz, len(sortie) - nz))

    with open(dst, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection",
                   "millesime": zonage["millesime"],
                   "features": sortie},
                  fh, ensure_ascii=False, separators=(",", ":"))
    print("%s — %.0f ko" % (dst, os.path.getsize(dst) / 1e3))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
