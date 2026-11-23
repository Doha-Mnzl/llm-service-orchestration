"""Spécification statique du jeu de données fictif (accessoires informatiques et électroniques).

Toutes les marques et tous les fournisseurs sont FICTIFS.
Montants exprimés dans la devise configurée (hypothèse D-1 : dirham marocain, MAD/DH).

P001–P006 et S101–S104 reprennent exactement les noms et catégories des faux services de
l'orchestrateur (orchestrator/mock_services.py, PR #1) pour garder la continuité des démos.
"""

# nom, libellé, fourchette de prix de vente, fourchette de demande quotidienne de base
CATEGORIES = [
    ("câbles", "Câbles", (40, 150), (3.0, 10.0)),
    ("chargeurs", "Chargeurs", (90, 350), (2.0, 7.0)),
    ("souris", "Souris", (60, 400), (1.0, 5.0)),
    ("claviers", "Claviers", (150, 900), (0.6, 3.5)),
    ("écouteurs", "Écouteurs", (60, 600), (1.0, 5.0)),
    ("casques", "Casques", (250, 1500), (0.3, 1.8)),
    ("webcams", "Webcams", (250, 1200), (0.3, 1.8)),
    ("stockage", "Stockage", (80, 1200), (1.0, 4.5)),
    ("hubs", "Hubs et adaptateurs", (120, 600), (0.5, 2.5)),
    ("batteries", "Batteries externes", (150, 600), (0.8, 3.5)),
]

BRANDS = ["Voltix", "Nordline", "Kebo", "Atlaz", "Sendo", "Pixa", "Orbik", "Lumen"]

# Modèles de noms par catégorie : (nom, caractéristiques)
TEMPLATES = {
    "câbles": [("Câble USB-C vers USB-C {n} m", {"connecteur": "USB-C", "longueur_m": "{n}"}),
               ("Câble HDMI 2.1 {n} m", {"connecteur": "HDMI", "longueur_m": "{n}"}),
               ("Câble Lightning {n} m", {"connecteur": "Lightning", "longueur_m": "{n}"}),
               ("Câble Ethernet Cat6 {n} m", {"connecteur": "RJ45", "longueur_m": "{n}"}),
               ("Câble USB-A vers USB-C {n} m", {"connecteur": "USB-A/USB-C", "longueur_m": "{n}"})],
    "chargeurs": [("Chargeur secteur {w} W", {"puissance_w": "{w}"}),
                  ("Chargeur GaN {w} W", {"puissance_w": "{w}", "technologie": "GaN"}),
                  ("Chargeur voiture {w} W", {"puissance_w": "{w}", "usage": "voiture"}),
                  ("Chargeur sans fil {w} W", {"puissance_w": "{w}", "technologie": "Qi"})],
    "souris": [("Souris sans fil {v}", {"connexion": "sans fil"}),
               ("Souris ergonomique {v}", {"connexion": "Bluetooth", "ergonomique": "oui"}),
               ("Souris gamer {v}", {"connexion": "filaire", "dpi": "16000"}),
               ("Souris filaire {v}", {"connexion": "filaire"})],
    "claviers": [("Clavier AZERTY sans fil {v}", {"disposition": "AZERTY", "connexion": "sans fil"}),
                 ("Clavier mécanique {v}", {"disposition": "AZERTY", "type": "mécanique"}),
                 ("Clavier compact Bluetooth {v}", {"disposition": "AZERTY", "connexion": "Bluetooth"})],
    "écouteurs": [("Écouteurs Bluetooth {v}", {"connexion": "Bluetooth"}),
                  ("Écouteurs à réduction de bruit {v}", {"connexion": "Bluetooth", "anc": "oui"}),
                  ("Écouteurs USB-C {v}", {"connexion": "USB-C"}),
                  ("Écouteurs sport {v}", {"connexion": "Bluetooth", "étanchéité": "IPX5"})],
    "casques": [("Casque sans fil {v}", {"connexion": "Bluetooth"}),
                ("Casque gamer {v}", {"connexion": "USB", "micro": "oui"}),
                ("Casque à réduction de bruit {v}", {"connexion": "Bluetooth", "anc": "oui"})],
    "webcams": [("Webcam Full HD {v}", {"résolution": "1080p"}),
                ("Webcam 4K {v}", {"résolution": "2160p"}),
                ("Webcam HD {v}", {"résolution": "720p"})],
    "stockage": [("Clé USB {g} Go", {"capacité_go": "{g}"}),
                 ("SSD externe {g} Go", {"capacité_go": "{g}", "type": "SSD"}),
                 ("Carte microSD {g} Go", {"capacité_go": "{g}"})],
    "hubs": [("Hub USB-C {p} ports", {"ports": "{p}"}),
             ("Adaptateur USB-C vers HDMI", {"sortie": "HDMI"}),
             ("Station d'accueil USB-C {p} ports", {"ports": "{p}"})],
    "batteries": [("Batterie externe {m} mAh", {"capacité_mah": "{m}"}),
                  ("Batterie externe MagSafe {m} mAh", {"capacité_mah": "{m}", "magnétique": "oui"}),
                  ("Batterie externe solaire {m} mAh", {"capacité_mah": "{m}"})],
}
VARIANTS = {"n": ["1", "2", "3", "0,5"], "w": ["20", "30", "45", "65", "100"], "v": ["V2", "Pro", "Lite", "S", "Max"],
            "g": ["32", "64", "128", "256", "512", "1000"], "p": ["4", "7", "10"],
            "m": ["5000", "10000", "20000"]}

# Produits du magasin repris des mocks : id → (nom, catégorie, prix, taux de marge)
MOCK_PRODUCTS = {
    "P001": ("Câble USB-C 1 m", "câbles", 70, 0.38),
    "P002": ("Chargeur 20 W", "chargeurs", 150, 0.31),
    "P003": ("Souris filaire basique", "souris", 70, 0.12),
    "P004": ("Clavier AZERTY USB", "claviers", 250, 0.27),
    "P005": ("Écouteurs filaires jack", "écouteurs", 80, 0.10),
    "P006": ("Câble micro-USB 2 m", "câbles", 50, 0.15),
}
MOCK_SUPPLIER_PRODUCTS = {
    "S101": ("Câble USB-C tressé 2 m", "câbles", 69),
    "S102": ("Chargeur GaN 35 W", "chargeurs", 195),
    "S103": ("Souris sans fil silencieuse", "souris", 120),
    "S104": ("Écouteurs Bluetooth", "écouteurs", 240),
}

PRODUCTS_PER_CATEGORY = 6          # 10 × 6 = 60 produits du magasin
SUPPLIER_PRODUCTS_TOTAL = 200      # LSO-21 : 200 références candidates (S101–S300)

SUPPLIERS = [  # id, nom fictif, pays, fiabilité
    ("F01", "Atlas Distribution", "Maroc", 0.95),
    ("F02", "Medina Tech Import", "Maroc", 0.88),
    ("F03", "Rif Electronics", "Maroc", 0.82),
    ("F04", "Iberia Components", "Espagne", 0.90),
    ("F05", "Lyon Accessoires Pro", "France", 0.93),
    ("F06", "Shenzhen Bright Supply", "Chine", 0.78),
    ("F07", "Anatolia Devices", "Turquie", 0.85),
    ("F08", "Delta Gadgets Wholesale", "Émirats arabes unis", 0.80),
]

# Scénarios imposés (les autres produits reçoivent un profil tiré au hasard, graine fixe).
#   decline_overstock : forte baisse des ventes + stock très élevé (candidat au retrait)
#   decline_stockout  : baisse + rupture en cours à la date de référence
#   dead              : plus aucune vente depuis 75 jours, stock restant (demande nulle)
#   supply_issue      : retard fournisseur ⇒ rupture passée, terminée
SCENARIOS = {
    "P001": "growth",
    "P002": "stable",
    "P003": "decline_overstock",
    "P004": "mild_decline",
    "P005": "decline_overstock",
    "P006": "decline_stockout",
    "P013": "dead",
    "P020": "supply_issue",
    "P031": "supply_issue",
    "P042": "decline_overstock",
    "P050": "decline_stockout",
}
RANDOM_PROFILES = [("stable", 0.5), ("growth", 0.2), ("mild_decline", 0.2), ("strong_seasonal", 0.1)]

# Saisonnalité mensuelle : rentrée (sept.), Black Friday (nov.), fêtes (déc.), creux d'été.
MONTH_FACTOR = {1: 0.9, 2: 0.85, 3: 0.95, 4: 1.0, 5: 1.0, 6: 0.9, 7: 0.8, 8: 0.85,
                9: 1.3, 10: 1.05, 11: 1.6, 12: 1.4}
WEEKDAY_FACTOR = {0: 0.95, 1: 0.95, 2: 1.0, 3: 1.0, 4: 1.1, 5: 1.3, 6: 0.7}
BLACK_FRIDAY_DISCOUNT = 0.15
