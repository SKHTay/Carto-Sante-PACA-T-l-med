Carte santé PACA : ZIP, zones blanches 4G, CPTS et zonage QPV.

Tous les fichiers sont à la racine du dépôt : index.html charge les données en
chemin relatif nu, sans sous-dossier.

DONNÉES
  communes-paca.geojson             947 communes PACA — 403 ZIP, 348 touchées par une zone blanche
  zones-blanches-4g.geojson         zones blanches 4G réelles — 2 039 km², 723 polygones (ARCEP T3 2024)
  cpts.geojson                      70 CPTS — 902 communes rattachées sur 947 (ARS PACA, 01/2026)
  arrondissements-marseille.geojson 16 arrondissements municipaux (13201-13216), maille des CPTS marseillaises
  qpv-zonage.json                   135 QPV — 67 en ZIP, 68 en ZAC, sur 53 communes (arrêté ARS PACA, zonage 2025)
  qpv-paca.geojson                  contours des 135 QPV de PACA (ANCT 2024), portant le zonage ZIP/ZAC

La carte se charge même si un de ces fichiers manque : la couche concernée
s'affiche alors désactivée, avec la marche à suivre.

SCRIPTS DE (RE)GÉNÉRATION
  build_data.py                     communes-paca.geojson + zones-blanches-4g.geojson
  build_cpts_from_xlsx.py           cpts.geojson, depuis le listing Excel de l'ARS
  build_arrondissements_marseille.py arrondissements-marseille.geojson
  build_qpv.py                      qpv-zonage.json, depuis l'arrêté de zonage QPV
  build_qpv_contours.py             qpv-paca.geojson, depuis l'export national ANCT
  convert_cpts.py, export_gpkg_paca.py  utilitaires amont

------------------------------------------------------------------------
qpv-zonage.json

Sorti du geojson communal à dessein : l'arrêté QPV est révisé à son propre
rythme, indépendamment des contours IGN et du millésime ARCEP. Le remplacer
ne demande donc pas de régénérer les 947 contours communaux (plusieurs Mo).

  python3 build_qpv.py "Arrêté_zonage_QPV_MEDECINS_PACA_2026_02.xlsx" \
      qpv-zonage.json communes-paca.geojson

Le troisième argument est facultatif ; s'il est fourni, le script vérifie que
tous les codes INSEE de l'arrêté ont un contour dans le référentiel et
signale ceux qui n'en ont pas (fusion de communes, coquille de saisie).

Ce fichier fait autorité sur le champ `q` porté par communes-paca.geojson,
que la page remet à zéro avant de l'appliquer.

Attention à ne pas confondre les deux zonages :
  - `z` (communes-paca.geojson) = zonage conventionnel MG de la COMMUNE,
    issu de l'extraction CartoSanté ; l'arrêté QPV ne le met pas à jour.
  - qpv-zonage.json = zonage prononcé QUARTIER par quartier. Une commune hors
    ZIP peut abriter un QPV classé ZIP, et une commune ZIP des QPV classés ZAC
    (zone d'action complémentaire). Nice, Aix et Toulon sont dans ce cas.

Quatre QPV sont à cheval sur plusieurs communes (Ranguin entre Cannes et Le
Cannet, Ariane sur trois communes niçoises, Les Moulins entre Nice et
Saint-Laurent-du-Var, Les Escourtines entre Marseille et La Penne-sur-Huveaune).
Ils sont rattachés à chacune d'elles ; les compteurs du panneau dédoublonnent
sur l'identifiant du QPV pour ne pas les compter deux fois.

------------------------------------------------------------------------
qpv-paca.geojson

Contours des quartiers, là où qpv-zonage.json ne porte que leur classement.
Les deux se complètent : le premier dit OÙ sont les quartiers, le second
COMMENT ils sont classés. Ils se remplacent indépendamment — la géographie des
QPV bouge tous les six ans, le zonage médical bien plus souvent.

Source : export national des quartiers prioritaires 2024 (ANCT, data.gouv.fr),
version WGS84. Prendre le fichier WGS84 et non le LB93 évite une reprojection :
Leaflet attend des longitudes et latitudes.

  python3 build_qpv_contours.py \
      QP2024_France_Hexagonale_Outre_Mer_WGS84.geojson \
      qpv-zonage.json qpv-paca.geojson

Le script filtre la région 93 (135 des 1 584 entités nationales), simplifie les
contours en Douglas-Peucker à 1,5e-5 degré (~1,7 m) et arrondit à 5 décimales :
243 ko en sortie, 31 % des sommets retirés sans perte de forme visible.

Il s'arrête si les deux jeux ne se recouvrent pas exactement. Un QPV sans
zonage s'afficherait en gris muet, un zonage sans contour disparaîtrait de la
carte : dans les deux cas mieux vaut échouer que publier.

Le zonage ZIP/ZAC ne figure pas dans l'export ANCT ; il est injecté depuis
l'arrêté au moment de la construction, ce qui évite une jointure côté client.
