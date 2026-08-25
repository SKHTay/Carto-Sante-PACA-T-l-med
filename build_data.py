#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prétraitement des données de la carte santé PACA.

Produit :
  data/communes-paca.geojson      947 communes, propriétés c/n/d/z/q/b
  data/zones-blanches-4g.geojson  zones blanches 4G réelles (aucun des 4 opérateurs)

Dépendances : shapely, pyproj  (pip install shapely pyproj)

Entrées attendues :
  --communes   communes-provence-alpes-cote-d-azur.geojson  (france-geojson, WGS84)
  --csv        data.csv  (extraction CartoSanté, séparateur ';', UTF-8 BOM)
  --gpkg       2024-t3-zone-blanche-metropole-4g-data.gpkg  (ARCEP, 342 Mo)
"""
import argparse, csv, json, os, sqlite3, struct, sys

DEPTS = ["04", "05", "06", "13", "83", "84"]
OPERATEURS = ["Orange", "ByTel", "Free", "SFR"]
PREC = 4          # décimales conservées (~11 m)
SIMPLIFY_M = 100  # tolérance de simplification, unités EPSG:3857 (~72 m réels à 44°N)
MIN_KM2 = 0.10    # superficie réelle minimale d'un polygone conservé


# ---------------------------------------------------------------- utilitaires
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
    """Réduction de précision. Préserve la topologie : deux sommets partagés
    par des communes voisines s'arrondissent à la même valeur, donc aucune
    fissure n'apparaît entre polygones adjacents (contrairement à simplify())."""
    t = g["type"]
    if t == "Polygon":
        rs = [r for r in (arrondi_ring(x) for x in g["coordinates"]) if r]
        return {"type": "Polygon", "coordinates": rs} if rs else None
    if t == "MultiPolygon":
        ps = []
        for poly in g["coordinates"]:
            rs = [r for r in (arrondi_ring(x) for x in poly) if r]
            if rs:
                ps.append(rs)
        return {"type": "MultiPolygon", "coordinates": ps} if ps else None
    return None


def wkb_depuis_gpkg(blob):
    """Un blob GeoPackage = en-tête binaire ('GP', version, flags, srs_id,
    enveloppe optionnelle) suivi du WKB standard. On saute l'en-tête."""
    assert blob[0:2] == b"GP", "en-tête GeoPackage absent"
    flags = blob[3]
    env = (flags >> 1) & 0x07
    tailles = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    return blob[8 + tailles[env]:]


# ------------------------------------------------------------------- communes
def charger_zip(chemin_csv):
    rows = list(csv.reader(open(chemin_csv, encoding="utf-8-sig"), delimiter=";"))[4:]
    info = {}
    for r in rows:
        if not r or not r[0].strip():
            continue
        qpv = r[4].strip() if len(r) > 4 else ""
        info[r[0].strip()] = {"qpv": int(qpv) if qpv.isdigit() else 0}
    return info


# -------------------------------------------------------------- zones blanches
def zones_blanches(chemin_gpkg):
    from shapely import wkb as shp_wkb
    from shapely.ops import transform, unary_union
    from shapely.geometry import mapping
    from pyproj import Transformer

    vers_wgs = Transformer.from_crs(3857, 4326, always_xy=True).transform
    # Les surfaces ne se calculent JAMAIS en EPSG:3857 : la projection Mercator
    # gonfle les aires de 1/cos²(latitude), soit ×1,93 à 44°N. Lambert-93 est la
    # projection officielle pour les mesures de surface en France métropolitaine.
    vers_l93 = Transformer.from_crs(3857, 2154, always_xy=True).transform
    def km2(g):
        return transform(vers_l93, g).area / 1e6

    con = sqlite3.connect(chemin_gpkg)
    feats = []

    for dep in DEPTS:
        geoms = []
        for op in OPERATEURS:
            row = con.execute(
                'SELECT geom FROM "%s" WHERE departement=?' % op, (dep,)
            ).fetchone()
            if row is None:
                print("  ! %s/%s absent" % (op, dep), file=sys.stderr)
                geoms = []
                break
            g = shp_wkb.loads(wkb_depuis_gpkg(row[0]))
            if not g.is_valid:
                g = g.buffer(0)
            geoms.append(g)
        if len(geoms) != 4:
            continue

        # ZONE BLANCHE RÉELLE = intersection des 4 opérateurs.
        # (l'union donnerait la zone grise : au moins un opérateur absent)
        inter = geoms[0]
        for g in geoms[1:]:
            inter = inter.intersection(g)
            if inter.is_empty:
                break
        if inter.is_empty:
            print("  %s : aucune zone blanche" % dep)
            continue

        brut = km2(inter)
        inter = inter.simplify(SIMPLIFY_M, preserve_topology=True).buffer(0)
        morceaux, ecartes, n_ecartes = [], 0.0, 0
        for p in getattr(inter, "geoms", [inter]):
            a = km2(p)
            if a >= MIN_KM2:
                morceaux.append(p)
            else:
                ecartes += a
                n_ecartes += 1
        if not morceaux:
            continue
        surface = round(sum(km2(p) for p in morceaux), 1)
        wgs = transform(vers_wgs, unary_union(morceaux))
        geom = arrondi_geom(mapping(wgs))
        if geom is None:
            continue
        feats.append({"type": "Feature",
                      "properties": {"d": dep, "km2": surface, "n": len(morceaux)},
                      "geometry": geom})
        # Rien n'est écarté en silence : le log dit ce qui a été retiré.
        print("  %s : brut %.1f km² → retenu %.1f km² sur %d polygones "
              "(écartés : %d polygones, %.2f km²)"
              % (dep, brut, surface, len(morceaux), n_ecartes, ecartes))
    return feats


# ----------------------------------------------------------------- croisement
def marquer_croisement(communes_feats, zb_feats):
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    if not zb_feats:
        return 0
    zones = [shape(f["geometry"]).buffer(0) for f in zb_feats]
    arbre = STRtree(zones)
    n = 0
    for f in communes_feats:
        g = shape(f["geometry"])
        touche = any(zones[i].intersects(g) for i in arbre.query(g))
        f["properties"]["b"] = 1 if touche else 0
        n += touche
    return n


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--communes", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--gpkg", default=None)
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    zipinfo = charger_zip(a.csv)
    print("Communes ZIP dans l'extraction CartoSanté : %d" % len(zipinfo))

    src = json.load(open(a.communes, encoding="utf-8"))
    feats = []
    for f in src["features"]:
        pr = f["properties"]
        g = arrondi_geom(f["geometry"])
        if g is None:
            continue
        z = zipinfo.get(pr["code"])
        feats.append({"type": "Feature",
                      "properties": {"c": pr["code"], "n": pr["nom"], "d": pr["code"][:2],
                                     "z": 1 if z else 0, "q": z["qpv"] if z else 0, "b": 0},
                      "geometry": g})
    codes = {f["properties"]["c"] for f in feats}
    orphelines = sorted(set(zipinfo) - codes)
    print("Contours chargés : %d | ZIP appariées : %d | ZIP sans contour : %s"
          % (len(feats), sum(f["properties"]["z"] for f in feats),
             orphelines or "aucune"))

    zb = []
    if a.gpkg:
        print("Zones blanches (intersection des 4 opérateurs) :")
        zb = zones_blanches(a.gpkg)
        total = round(sum(f["properties"]["km2"] for f in zb), 1)
        print("  TOTAL PACA : %s km²" % total)
        # PACA = 31 400 km². Référence mesurée sur le millésime T3 2024 :
        # 2 039 km², soit 6,5 % du territoire régional.
        if not (500 < total < 6000):
            print("  ATTENTION : total hors de l'ordre de grandeur attendu "
                  "(~2 000 km²) — vérifier intersection vs union, et que les "
                  "surfaces sont bien calculées en Lambert-93 et non en Mercator",
                  file=sys.stderr)
        n = marquer_croisement(feats, zb)
        nz = sum(1 for f in feats
                 if f["properties"]["b"] == 1 and f["properties"]["z"] == 1)
        print("  Communes touchées : %d — dont ZIP : %d" % (n, nz))
        json.dump({"type": "FeatureCollection", "features": zb},
                  open(os.path.join(a.out, "zones-blanches-4g.geojson"), "w"),
                  ensure_ascii=False, separators=(",", ":"))

    json.dump({"type": "FeatureCollection", "features": feats},
              open(os.path.join(a.out, "communes-paca.geojson"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    for nom in ("communes-paca.geojson", "zones-blanches-4g.geojson"):
        p = os.path.join(a.out, nom)
        if os.path.exists(p):
            print("%-30s %6.2f Mo" % (nom, os.path.getsize(p) / 1e6))


if __name__ == "__main__":
    main()
