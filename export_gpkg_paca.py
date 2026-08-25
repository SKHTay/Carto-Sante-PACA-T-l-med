#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extrait du GeoPackage ARCEP national un GeoPackage valide limité à PACA.

  342 Mo / 382 entités  →  35,6 Mo / 24 entités  (4 couches × 6 départements)

Aucune dépendance : sqlite3 seul, pas de GDAL, pas de shapely. Les géométries
sont recopiées telles quelles (blobs identiques à l'octet près), donc aucune
perte de précision.

Usage :
    python3 tools/export_gpkg_paca.py source.gpkg sortie.gpkg

Trois pièges que ce script traite explicitement — les trois font qu'un
GeoPackage recopié « naïvement » s'ouvre en erreur dans QGIS :

1. Les tables fantômes de l'index spatial (rtree_*_rowid / _node / _parent)
   ne se recréent pas à la main : c'est la table virtuelle rtree_*_geom qui
   les fabrique. On saute les premières et on crée la seconde.
2. Les déclencheurs qui maintiennent l'index appellent ST_MinX, ST_IsEmpty…,
   fonctions SQL fournies par GDAL et absentes du sqlite3 de Python. On les
   réimplémente en lisant l'enveloppe dans l'en-tête du blob GeoPackage.
3. La signature « GPKG » vit dans application_id, un champ de l'en-tête du
   fichier SQLite qu'une base neuve laisse à zéro. Sans elle, le fichier est
   une base SQLite quelconque et aucun SIG ne le reconnaît.
"""
import os, sqlite3, struct, sys, time

DEPTS = ("04", "05", "06", "13", "83", "84")
COUCHES = ["Orange", "ByTel", "Free", "SFR"]
SHADOW = ("_rowid", "_node", "_parent")
GPKG_APPLICATION_ID = 1196444487   # 'GPKG' en big-endian
GPKG_USER_VERSION = 10200          # version 1.2


def enveloppe(blob):
    """(min_x, max_x, min_y, max_y) lus dans l'en-tête GeoPackage, ou None."""
    if blob is None or len(blob) < 8 or blob[0:2] != b"GP":
        return None
    if ((blob[3] >> 1) & 0x07) != 1:   # indicateur d'enveloppe : 1 = XY
        return None
    return struct.unpack_from("<4d", blob, 8)


def est_vide(blob):
    return 1 if (blob is None or len(blob) < 8 or ((blob[3] >> 2) & 1)) else 0


def main(src_path, dst_path):
    t0 = time.time()
    if os.path.exists(dst_path):
        os.remove(dst_path)
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)

    # Les fonctions spatiales attendues par les déclencheurs de l'index rtree.
    dst.create_function("ST_IsEmpty", 1, est_vide)
    for i, nom in enumerate(["ST_MinX", "ST_MaxX", "ST_MinY", "ST_MaxY"]):
        dst.create_function(
            nom, 1, (lambda k: lambda g: (enveloppe(g) or (0, 0, 0, 0))[k])(i))

    objets = list(src.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"))

    def ignorer(nom):
        return nom == "sqlite_sequence" or (
            nom.startswith("rtree_") and nom.endswith(SHADOW))

    def creer(predicat):
        for typ, nom, sql in objets:
            if ignorer(nom) or not predicat(typ, sql):
                continue
            try:
                dst.execute(sql)
            except sqlite3.OperationalError as e:
                print("  ignoré : %s (%s)" % (nom, e), file=sys.stderr)

    virt = lambda sql: sql.lstrip().upper().startswith("CREATE VIRTUAL")
    creer(lambda t, sql: t == "table" and not virt(sql))   # tables ordinaires
    creer(lambda t, sql: t == "table" and virt(sql))       # index rtree
    creer(lambda t, sql: t == "index")
    creer(lambda t, sql: t == "trigger")
    dst.commit()

    # Métadonnées gpkg_* : recopiées intégralement.
    for typ, nom, sql in objets:
        if typ != "table" or not nom.startswith("gpkg_"):
            continue
        rows = src.execute('SELECT * FROM "%s"' % nom).fetchall()
        if rows:
            dst.executemany('INSERT INTO "%s" VALUES(%s)'
                            % (nom, ",".join("?" * len(rows[0]))), rows)
    dst.commit()

    marques = ",".join("?" * len(DEPTS))
    total = 0
    for couche in COUCHES:
        cols = [r[1] for r in src.execute('PRAGMA table_info("%s")' % couche)]
        rows = src.execute(
            'SELECT * FROM "%s" WHERE departement IN (%s) ORDER BY departement'
            % (couche, marques), DEPTS).fetchall()
        dst.executemany('INSERT INTO "%s" VALUES(%s)'
                        % (couche, ",".join("?" * len(cols))), rows)

        gi = cols.index("geom")
        xs, ys = [], []
        for r in rows:
            e = enveloppe(r[gi])
            if e:
                xs += [e[0], e[1]]
                ys += [e[2], e[3]]
        if xs:
            dst.execute("UPDATE gpkg_contents SET min_x=?, max_x=?, min_y=?, "
                        "max_y=? WHERE table_name=?",
                        (min(xs), max(xs), min(ys), max(ys), couche))
        dst.execute("UPDATE gpkg_ogr_contents SET feature_count=? "
                    "WHERE table_name=?", (len(rows), couche))
        n_rtree = dst.execute(
            "SELECT count(*) FROM rtree_%s_geom" % couche).fetchone()[0]
        print("%-8s %2d entités | index spatial %2d" % (couche, len(rows), n_rtree))
        total += len(rows)
    dst.commit()

    dst.execute("PRAGMA application_id=%d" % GPKG_APPLICATION_ID)
    dst.execute("PRAGMA user_version=%d" % GPKG_USER_VERSION)
    dst.commit()
    dst.execute("VACUUM")

    # Contrôle : les géométries doivent être identiques à l'octet près.
    ecarts = 0
    for couche in COUCHES:
        for dep in DEPTS:
            a = src.execute('SELECT geom FROM "%s" WHERE departement=?'
                            % couche, (dep,)).fetchone()
            b = dst.execute('SELECT geom FROM "%s" WHERE departement=?'
                            % couche, (dep,)).fetchone()
            if (a and a[0]) != (b and b[0]):
                ecarts += 1
                print("  ÉCART : %s / %s" % (couche, dep), file=sys.stderr)
    print("Contrôle : %d entités, %d écart(s), intégrité SQLite %s"
          % (total, ecarts, dst.execute("PRAGMA quick_check").fetchone()[0]))
    dst.close()
    print("%s — %.1f Mo en %.0fs"
          % (dst_path, os.path.getsize(dst_path) / 1e6, time.time() - t0))
    return 1 if ecarts else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
