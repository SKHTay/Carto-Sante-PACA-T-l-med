#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests fonctionnels de index.html dans un vrai navigateur (Chromium)."""
import http.server
import json
import os
import socketserver
import sys
import threading

from playwright.sync_api import sync_playwright

RACINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
ECHECS = []


def ok(nom, cond, detail=""):
    print(("  OK   " if cond else "  ÉCHEC ") + nom + (("  — " + str(detail)) if detail else ""))
    if not cond:
        ECHECS.append(nom + " " + str(detail))


class Muet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=RACINE, **k)

    def log_message(self, *a):
        pass


def serveur():
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Muet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def ouvrir_cpts(pg):
    """Rouvre le panneau CPTS s'il est replié.

    Tout clic hors de la section (case d'une autre couche, lien départements,
    touche Échap, clic sur la carte) le referme — c'est le comportement voulu
    d'un menu déroulant. Les tests doivent donc le rouvrir avant d'y toucher.
    """
    if not pg.is_visible("#cpts-menu"):
        pg.click("#cpts-toggle")
        pg.wait_for_timeout(200)


def nombre(txt):
    return int(txt.replace("\u202f", "").replace("\u00a0", "").replace(" ", "") or 0)


def main(sans_qpv=False, sans_cpts=False, sans_contours=False):
    srv, port = serveur()
    attendu = json.load(open(os.path.join(RACINE, "qpv-zonage.json"), encoding="utf-8"))

    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        pg = nav.new_page(viewport={"width": 1400, "height": 900})
        erreurs, logs = [], []
        pg.on("pageerror", lambda e: erreurs.append(str(e)))
        pg.on("console", lambda m: logs.append(m.type + ": " + m.text))
        # Les fonds de tuiles sont hors réseau ici : on les court-circuite.
        pg.route("**/*.png", lambda r: r.abort() if "http" in r.request.url and
                 "127.0.0.1" not in r.request.url else r.continue_())

        url = "http://127.0.0.1:%d/index.html" % port
        if sans_qpv:
            pg.route("**/qpv-zonage.json", lambda r: r.fulfill(status=404, body="nope"))
        if sans_cpts:
            pg.route("**/cpts.geojson", lambda r: r.fulfill(status=404, body="nope"))
        if sans_contours:
            pg.route("**/qpv-paca.geojson", lambda r: r.fulfill(status=404, body="nope"))
        pg.goto(url)
        pg.wait_for_function("document.getElementById('loader').classList.contains('done')",
                             timeout=20000)
        pg.wait_for_timeout(1200)

        titre = ("SANS QPV" if sans_qpv else "SANS CPTS" if sans_cpts
                 else "SANS CONTOURS QPV" if sans_contours else "NOMINAL")
        print("\n=== %s ===" % titre)
        ok("aucune erreur JS", not erreurs, erreurs[:3])

        # ---------------------------------------------------------- données QPV
        if sans_qpv:
            ok("compteur QPV neutralisé", pg.inner_text("#s-qzip") == "-")
            ok("message de repli affiché", "build_qpv" in pg.inner_text("#qpv-note"))
        else:
            def distincts(codes, zon):
                return len({q["id"] for c in codes
                            for q in attendu["communes"].get(c, {}).get("qpv", [])
                            if q["z"] == zon})
            tous = list(attendu["communes"])
            tot_zip, tot_zac = distincts(tous, "ZIP"), distincts(tous, "ZAC")
            pg.click('[data-all="1"]')
            pg.check("#l-non")
            pg.wait_for_timeout(300)
            ok("total QPV ZIP distincts (toutes communes)",
               nombre(pg.inner_text("#s-qzip")) == tot_zip,
               "%s vs %d" % (pg.inner_text("#s-qzip"), tot_zip))
            ok("total QPV ZAC distincts (toutes communes)",
               nombre(pg.inner_text("#s-qzac")) == tot_zac,
               "%s vs %d" % (pg.inner_text("#s-qzac"), tot_zac))
            ok("note QPV : millésime et volumes",
               "135 QPV" in pg.inner_text("#qpv-note")
               and "zonage 2025" in pg.inner_text("#qpv-note"),
               pg.inner_text("#qpv-note"))
            ok("aucun code INSEE orphelin",
               not any("QPV sans contour" in l for l in logs))

            # Filtre départemental : les compteurs QPV doivent suivre.
            pg.click('[data-all="0"]')
            pg.check('#depts input[value="84"]')
            pg.wait_for_timeout(300)
            z84 = distincts([c for c in tous if c.startswith("84")], "ZIP")
            ok("QPV ZIP recalculés sur le Vaucluse seul",
               nombre(pg.inner_text("#s-qzip")) == z84,
               "%s vs %d" % (pg.inner_text("#s-qzip"), z84))
            pg.click('[data-all="1"]')
            pg.wait_for_timeout(300)

            # Popup d'une commune à QPV mixtes (Marseille : 14 ZIP, 27 ZAC).
            pg.fill("#q", "13055")
            pg.dispatch_event("#q", "change")
            pg.wait_for_timeout(900)
            popup = pg.inner_text(".leaflet-popup-content")
            ok("popup : décompte ZIP/ZAC", "14 ZIP" in popup and "27 ZAC" in popup, popup[:90])
            ok("popup : QPV nommés",
               "Saint Henri" in popup and pg.locator(".qpvlist li").count() == 41,
               pg.locator(".qpvlist li").count())
            pg.keyboard.press("Escape")

        # ------------------------------------------------- contours des QPV
        contours = json.load(open(os.path.join(RACINE, "qpv-paca.geojson"),
                                  encoding="utf-8"))["features"]
        if sans_contours:
            ok("couche QPV désactivée sans contours", pg.is_disabled("#l-qpv"))
            ok("compteur signale l'import", pg.inner_text("#c-qpv") == "à importer")
            ok("aucun contour tracé",
               pg.evaluate("document.querySelectorAll('path[data-qpv]').length") == 0)
        else:
            pg.click('[data-all="1"]')
            pg.wait_for_timeout(400)
            ok("compteur de contours QPV",
               pg.inner_text("#c-qpv") == str(len(contours)), pg.inner_text("#c-qpv"))
            ok("case QPV cochée par défaut", pg.is_checked("#l-qpv"))

            n_avec = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            pg.uncheck("#l-qpv")
            pg.wait_for_timeout(400)
            n_sans = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            ok("décocher retire bien %d tracés" % len(contours),
               n_avec - n_sans == len(contours), "%d - %d" % (n_avec, n_sans))
            pg.check("#l-qpv")
            pg.wait_for_timeout(400)

            # Le croisement doit masquer les quartiers.
            pg.click("#cross")
            pg.wait_for_timeout(400)
            n_cross = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            pg.click("#cross")
            pg.wait_for_timeout(400)
            ok("croisement : contours QPV masqués",
               n_cross <= n_avec - len(contours), "%d vs %d" % (n_cross, n_avec))

            # Filtre départemental appliqué aux quartiers.
            pg.click('[data-all="0"]')
            pg.check('#depts input[value="13"]')
            pg.wait_for_timeout(500)
            n13 = sum(1 for f in contours if f["properties"]["d"] == "13")
            n_dep = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            pg.uncheck("#l-qpv")
            pg.wait_for_timeout(400)
            n_dep_sans = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            ok("seuls les QPV des Bouches-du-Rhône sont tracés",
               n_dep - n_dep_sans == n13, "%d vs %d" % (n_dep - n_dep_sans, n13))
            pg.check("#l-qpv")
            pg.click('[data-all="1"]')
            pg.wait_for_timeout(400)

        # ------------------------------------------------ sélection des CPTS
        if sans_cpts:
            ok("bouton désactivé sans données", pg.is_disabled("#cpts-toggle"))
            ok("libellé d'indisponibilité",
               "indisponible" in pg.inner_text("#cpts-toggle"))
            ok("consigne de dépôt du fichier",
               "cpts.geojson" in pg.inner_text("#cpts-note"))
            ok("case restriction inactive", pg.is_disabled("#l-restr"))
        else:
            cpts = json.load(open(os.path.join(RACINE, "cpts.geojson"), encoding="utf-8"))
            noms = [f["properties"]["nom"] for f in cpts["features"]]
            ok("compteur CPTS", pg.inner_text("#c-cpts") == str(len(noms)))
            ok("toutes cochées par défaut",
               pg.inner_text("#cpts-toggle") == "Toutes les CPTS (%d)" % len(noms),
               pg.inner_text("#cpts-toggle"))
            ok("panneau replié au départ", not pg.is_visible("#cpts-menu"))

            pg.click("#cpts-toggle")
            pg.wait_for_timeout(250)
            ok("le bouton déplie le panneau", pg.is_visible("#cpts-menu"))
            ok("une case par CPTS",
               pg.locator("#cpts-list input").count() == len(noms))
            ok("regroupement par département",
               pg.locator("#cpts-list .grp").count() >= 5,
               pg.locator("#cpts-list .grp").count())
            ok("toutes les cases sont cochées",
               pg.locator("#cpts-list input:checked").count() == len(noms))

            n_toutes = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")

            # --- sélection multiple : c'est le cœur du changement.
            ouvrir_cpts(pg)
            pg.click('[data-cpts-all="0"]')
            pg.wait_for_timeout(400)
            ok("« Aucune » vide la sélection",
               pg.inner_text("#cpts-toggle") == "Aucune CPTS affichée",
               pg.inner_text("#cpts-toggle"))
            n_zero = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            ok("plus aucun tracé de CPTS", n_zero < n_toutes)

            idx_arr = next(i for i, f in enumerate(cpts["features"])
                           if f["properties"].get("arrondissements"))
            idx_sans = [i for i, f in enumerate(cpts["features"])
                        if not f["properties"].get("arrondissements")]
            a, b_, c_ = idx_sans[0], idx_sans[1], idx_sans[2]

            ouvrir_cpts(pg)
            pg.check('#cpts-list input[value="%d"]' % a)
            pg.wait_for_timeout(350)
            ok("une seule cochée : le bouton affiche son nom",
               pg.inner_text("#cpts-toggle") == noms[a], pg.inner_text("#cpts-toggle"))
            ok("fiche affichée pour une CPTS unique", pg.is_visible("#cpts-fiche"))
            ok("fiche : bon nom", noms[a] in pg.inner_text("#cpts-fiche"))
            n_une = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")

            ouvrir_cpts(pg)
            pg.check('#cpts-list input[value="%d"]' % b_)
            ouvrir_cpts(pg)
            pg.check('#cpts-list input[value="%d"]' % c_)
            pg.wait_for_timeout(400)
            ok("trois cochées : le bouton compte",
               pg.inner_text("#cpts-toggle") == "3 CPTS sélectionnées",
               pg.inner_text("#cpts-toggle"))
            ok("fiche masquée au-delà d'une CPTS", not pg.is_visible("#cpts-fiche"))
            n_trois = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            ok("trois CPTS tracées, pas une", n_trois == n_une + 2,
               "%d vs %d" % (n_trois, n_une + 2))

            # Décocher n'affecte que la CPTS visée.
            ouvrir_cpts(pg)
            pg.uncheck('#cpts-list input[value="%d"]' % b_)
            pg.wait_for_timeout(350)
            ok("décocher retire une seule CPTS",
               pg.inner_text("#cpts-toggle") == "2 CPTS sélectionnées")
            ok("les deux autres restent cochées",
               pg.is_checked('#cpts-list input[value="%d"]' % a)
               and pg.is_checked('#cpts-list input[value="%d"]' % c_))

            # Arrondissements : visibles seulement si une CPTS marseillaise
            # figure dans la sélection.
            n_hors = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            ouvrir_cpts(pg)
            pg.check('#cpts-list input[value="%d"]' % idx_arr)
            pg.wait_for_timeout(450)
            n_avec = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            ok("arrondissements ajoutés avec une CPTS marseillaise",
               n_avec - n_hors > 16, "%d vs %d" % (n_avec, n_hors))
            ouvrir_cpts(pg)
            pg.uncheck('#cpts-list input[value="%d"]' % idx_arr)
            pg.wait_for_timeout(350)

            # --- filtre de recherche
            pg.fill("#cpts-q", "marseille")
            pg.wait_for_timeout(300)
            vis = pg.locator("#cpts-list label:not([hidden])").count()
            ok("le filtre réduit la liste", 0 < vis < len(noms), vis)
            ok("les cases cochées ne bougent pas au filtrage",
               pg.inner_text("#cpts-toggle") == "2 CPTS sélectionnées")
            ok("compteur « n sur total » affiché",
               "sur %d" % len(noms) in pg.inner_text("#cpts-vus"),
               pg.inner_text("#cpts-vus"))

            # « Toutes » n'agit que sur les lignes visibles.
            ouvrir_cpts(pg)
            pg.click('[data-cpts-all="1"]')
            pg.wait_for_timeout(400)
            n_sel = pg.locator("#cpts-list input:checked").count()
            ok("« Toutes » se limite au filtre courant", n_sel < len(noms), n_sel)

            pg.fill("#cpts-q", "zzzz")
            pg.wait_for_timeout(300)
            ok("message quand le filtre ne renvoie rien",
               pg.is_visible("#cpts-list .vide"))
            pg.fill("#cpts-q", "")
            pg.wait_for_timeout(300)
            ok("vider le filtre réaffiche tout",
               pg.locator("#cpts-list label:not([hidden])").count() == len(noms))

            # --- restriction de la sélection au périmètre cumulé
            ouvrir_cpts(pg)
            pg.click('[data-cpts-all="0"]')
            pg.wait_for_timeout(300)
            ok("restriction inactive sans CPTS", pg.is_disabled("#l-restr"))
            ouvrir_cpts(pg)
            pg.check('#cpts-list input[value="%d"]' % a)
            ouvrir_cpts(pg)
            pg.check('#cpts-list input[value="%d"]' % b_)
            pg.wait_for_timeout(400)
            ok("restriction activable dès une sélection partielle",
               not pg.is_disabled("#l-restr"))
            pg.check("#l-non")
            pg.check("#l-restr")
            pg.wait_for_timeout(600)
            union = set(cpts["features"][a]["properties"]["communes"]) | \
                    set(cpts["features"][b_]["properties"]["communes"])
            ok("le périmètre est l'union des CPTS cochées",
               nombre(pg.inner_text("#s-tot")) == len(union),
               "%s vs %d" % (pg.inner_text("#s-tot"), len(union)))
            if not sans_qpv:
                zc = distincts(list(union), "ZIP")
                ok("QPV ZIP recalculés sur l'union",
                   nombre(pg.inner_text("#s-qzip")) == zc,
                   "%s vs %d" % (pg.inner_text("#s-qzip"), zc))

            # Ajouter une CPTS élargit le périmètre sans le recalculer à tort.
            ouvrir_cpts(pg)
            pg.check('#cpts-list input[value="%d"]' % c_)
            pg.wait_for_timeout(500)
            union3 = union | set(cpts["features"][c_]["properties"]["communes"])
            ok("le périmètre suit l'ajout d'une CPTS",
               nombre(pg.inner_text("#s-tot")) == len(union3),
               "%s vs %d" % (pg.inner_text("#s-tot"), len(union3)))

            # La recherche doit lever la restriction si la commune est dehors.
            dehors = next(f["properties"]["c"] for f in
                          json.load(open(os.path.join(RACINE, "communes-paca.geojson"),
                                         encoding="utf-8"))["features"]
                          if f["properties"]["c"] not in union3)
            pg.fill("#q", dehors)
            pg.dispatch_event("#q", "change")
            pg.wait_for_timeout(800)
            ok("recherche hors périmètre : restriction levée",
               not pg.is_checked("#l-restr"))
            ok("commune recherchée bien affichée",
               pg.locator(".leaflet-popup-content").count() == 1)
            pg.keyboard.press("Escape")

            # Un département décoché doit se rouvrir si on y coche une CPTS.
            ouvrir_cpts(pg)
            pg.click('[data-cpts-all="0"]')
            pg.wait_for_timeout(300)
            pg.click('[data-all="0"]')
            pg.wait_for_timeout(250)
            ouvrir_cpts(pg)
            pg.check('#cpts-list input[value="%d"]' % c_)
            pg.wait_for_timeout(500)
            dep = cpts["features"][c_]["properties"]["communes"][0][:2]
            ok("département rouvert automatiquement",
               pg.is_checked('#depts input[value="%s"]' % dep))

            pg.click('[data-all="1"]')
            ouvrir_cpts(pg)
            pg.click('[data-cpts-all="1"]')
            pg.wait_for_timeout(400)
            ok("retour à toutes les CPTS",
               pg.inner_text("#cpts-toggle") == "Toutes les CPTS (%d)" % len(noms))

            # Le panneau se referme au clic extérieur.
            pg.click("#map", position={"x": 400, "y": 300})
            pg.wait_for_timeout(300)
            ok("clic hors du panneau : menu replié", not pg.is_visible("#cpts-menu"))
            pg.keyboard.press("Escape")

        # ---------------------------------------------------------- croisement
        pg.click("#cross")
        pg.wait_for_timeout(500)
        ok("croisement : ZIP et zone blanche seulement",
           nombre(pg.inner_text("#s-tot")) == nombre(pg.inner_text("#s-both")),
           pg.inner_text("#s-tot") + "/" + pg.inner_text("#s-both"))
        pg.click("#cross")
        pg.wait_for_timeout(400)

        # ---------------------------------------------------------- export CSV
        with pg.expect_download() as dl:
            pg.click("#csv")
        chemin = dl.value.path()
        csv = open(chemin, encoding="utf-8-sig").read().splitlines()
        entete = csv[0].split(";")
        ok("CSV : colonnes qpv_zip / qpv_zac / cpts",
           entete[4:6] == ["qpv_zip", "qpv_zac"] and entete[-1] == "cpts", entete)
        ok("CSV : une ligne par commune affichée",
           len(csv) - 1 == nombre(pg.inner_text("#s-tot")),
           "%d vs %s" % (len(csv) - 1, pg.inner_text("#s-tot")))
        if not sans_qpv:
            ligne = [l for l in csv if l.startswith("13055;")]
            ok("CSV : Marseille porte 14 ZIP / 27 ZAC",
               ligne and ligne[0].split(";")[4:6] == ["14", "27"],
               ligne[0][:70] if ligne else "absente")

        ok("aucune erreur JS en fin de parcours", not erreurs, erreurs[:3])
        graves = [l for l in logs if l.startswith("error")
                  and "Failed to load resource" not in l]
        ok("console sans erreur", not graves, graves[:3])
        nav.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
    main(sans_qpv=True)
    main(sans_cpts=True)
    main(sans_contours=True)
    print("\n" + ("=" * 50))
    if ECHECS:
        print("%d ÉCHEC(S) :" % len(ECHECS))
        for e in ECHECS:
            print("  -", e)
        sys.exit(1)
    print("Tous les tests passent.")
