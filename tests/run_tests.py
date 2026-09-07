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

        # ---------------------------------------------------------- menu CPTS
        opts = pg.locator("#cpts-select option")
        if sans_cpts:
            ok("menu désactivé sans données", pg.is_disabled("#cpts-select"))
            ok("consigne de dépôt du fichier",
               "cpts.geojson" in pg.inner_text("#cpts-note"))
            ok("case restriction inactive", pg.is_disabled("#l-restr"))
        else:
            cpts = json.load(open(os.path.join(RACINE, "cpts.geojson"), encoding="utf-8"))
            noms = [f["properties"]["nom"] for f in cpts["features"]]
            ok("menu peuplé : 2 entrées globales + %d CPTS" % len(noms),
               opts.count() == len(noms) + 2, opts.count())
            ok("compteur CPTS", pg.inner_text("#c-cpts") == str(len(noms)))
            ok("regroupement par département",
               pg.locator("#cpts-select optgroup").count() >= 5,
               pg.locator("#cpts-select optgroup").count())
            ok("valeur par défaut = toutes", pg.input_value("#cpts-select") == "*")

            n_toutes = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")

            # Sélection d'une CPTS marseillaise (celle qui a des arrondissements).
            idx = next(i for i, f in enumerate(cpts["features"])
                       if f["properties"].get("arrondissements"))
            pg.select_option("#cpts-select", str(idx))
            pg.wait_for_timeout(700)
            ok("fiche CPTS affichée", pg.is_visible("#cpts-fiche"))
            fiche = pg.inner_text("#cpts-fiche")
            ok("fiche : nom de la CPTS", noms[idx] in fiche, fiche[:60])
            ok("fiche : arrondissements en clair",
               "1er" in fiche or "e" in fiche, fiche)
            ok("case restriction activée", not pg.is_disabled("#l-restr"))

            n_une = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            ok("moins de tracés qu'en mode « toutes »", n_une < n_toutes,
               "%d vs %d" % (n_une, n_toutes))

            # Une CPTS sans arrondissement ne doit pas traîner les 16 contours.
            idx2 = next(i for i, f in enumerate(cpts["features"])
                        if not f["properties"].get("arrondissements"))
            pg.select_option("#cpts-select", str(idx2))
            pg.wait_for_timeout(600)
            n_sans_arr = pg.evaluate(
                "document.querySelectorAll('path.leaflet-interactive').length")
            ok("arrondissements masqués hors Marseille", n_sans_arr < n_une,
               "%d vs %d" % (n_sans_arr, n_une))

            # Restriction de la sélection au périmètre de la CPTS.
            codes = cpts["features"][idx2]["properties"]["communes"]
            pg.check("#l-non")
            pg.check("#l-restr")
            pg.wait_for_timeout(600)
            ok("stat « communes affichées » = périmètre CPTS",
               nombre(pg.inner_text("#s-tot")) == len(codes),
               "%s vs %d" % (pg.inner_text("#s-tot"), len(codes)))
            if not sans_qpv:
                zc = distincts(codes, "ZIP")
                ok("QPV ZIP recalculés sur le périmètre CPTS",
                   nombre(pg.inner_text("#s-qzip")) == zc,
                   "%s vs %d" % (pg.inner_text("#s-qzip"), zc))

            # La recherche doit lever la restriction si la commune est dehors.
            dehors = next(f["properties"]["c"] for f in
                          json.load(open(os.path.join(RACINE, "communes-paca.geojson"),
                                         encoding="utf-8"))["features"]
                          if f["properties"]["c"] not in codes)
            pg.fill("#q", dehors)
            pg.dispatch_event("#q", "change")
            pg.wait_for_timeout(800)
            ok("recherche hors périmètre : restriction levée",
               not pg.is_checked("#l-restr"))
            ok("commune recherchée bien affichée",
               pg.locator(".leaflet-popup-content").count() == 1)
            pg.keyboard.press("Escape")

            # Retour à « masquer ».
            pg.select_option("#cpts-select", "")
            pg.wait_for_timeout(500)
            ok("fiche masquée", not pg.is_visible("#cpts-fiche"))
            ok("restriction remise à zéro", not pg.is_checked("#l-restr")
               and pg.is_disabled("#l-restr"))

            # Un département décoché doit se rouvrir si on y choisit une CPTS.
            pg.click('[data-all="0"]')
            pg.wait_for_timeout(200)
            pg.select_option("#cpts-select", str(idx2))
            pg.wait_for_timeout(600)
            dep = cpts["features"][idx2]["properties"]["communes"][0][:2]
            ok("département rouvert automatiquement",
               pg.is_checked('#depts input[value="%s"]' % dep))
            pg.click('[data-all="1"]')
            pg.wait_for_timeout(300)

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
