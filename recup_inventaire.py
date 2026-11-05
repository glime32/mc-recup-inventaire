"""
Récupère le stuff d'un joueur (inventaire, armure, ender chest, position,
stats, progrès) depuis la sauvegarde .tar.gz d'un serveur (Paper, Purpur...)
et le remet dans une copie solo du monde.

Exemple :
    pip install nbtlib
    python recup_inventaire.py --archive sauvegarde.tar.gz --monde-nom survie \
        --pseudo Steve

Utile quand on récupère le monde d'un serveur fermé : en solo le jeu crée
un joueur vide dans level.dat et ignore le fichier playerdata du serveur.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

try:
    import nbtlib
except ImportError:
    sys.exit("Il manque nbtlib :  pip install nbtlib")


def uuid_hors_ligne(pseudo):
    """UUID qu'un serveur en online-mode=false donne à un pseudo."""
    h = bytearray(hashlib.md5(f"OfflinePlayer:{pseudo}".encode()).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(h)))


def uuid_mojang(pseudo):
    url = f"https://api.mojang.com/users/profiles/minecraft/{pseudo}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return str(uuid.UUID(json.load(r)["id"]))
    except Exception:
        return None


def racine(fichier_nbt):
    # nbtlib 1.x met tout sous la clé "", pas la 2.x
    if set(fichier_nbt.keys()) == {""}:
        return fichier_nbt[""]
    return fichier_nbt


def trouver_monde(nom, chemin):
    if chemin:
        monde = Path(chemin)
        if not (monde / "level.dat").exists():
            sys.exit(f"Pas de level.dat dans {monde}")
        return monde
    # Windows : profils Modrinth App + .minecraft classique
    appdata = Path(os.environ.get("APPDATA", Path.home() / ".local/share"))
    candidats = list((appdata / "ModrinthApp" / "profiles").glob(f"*/saves/{nom}"))
    candidats.append(appdata / ".minecraft" / "saves" / nom)
    candidats.append(Path.home() / ".minecraft" / "saves" / nom)
    candidats = [c for c in candidats if (c / "level.dat").exists()]
    if not candidats:
        sys.exit(f"Monde {nom} introuvable, donne son chemin avec --monde.")
    # d'abord un monde réparé (world_gen_settings.dat présent), puis le plus récent
    candidats.sort(
        key=lambda c: (
            (c / "data" / "minecraft" / "world_gen_settings.dat").exists(),
            (c / "level.dat").stat().st_mtime,
        ),
        reverse=True,
    )
    return candidats[0]


def lire_archive(archive, nom_monde, uuids, uuid_cible):
    trouves, fichiers_joueurs = [], []
    print("Lecture de l'archive (ça peut prendre quelques minutes)...")
    with tarfile.open(archive, "r:gz") as tar:
        for i, membre in enumerate(tar, 1):
            if i % 1000 == 0:
                print(f"  {i} fichiers parcourus", end="\r")
            if not membre.isfile():
                continue
            parties = [p for p in membre.name.replace("\\", "/").split("/") if p not in ("", ".")]
            if nom_monde not in parties:
                continue
            relatif = parties[parties.index(nom_monde) + 1:]
            if not relatif:
                continue
            if relatif[0] in ("players", "playerdata"):
                fichiers_joueurs.append("/".join(relatif))
            nom = relatif[-1]
            for u in uuids:
                if u in nom:
                    # un fichier au nom de l'UUID "crack" est renommé avec le vrai
                    relatif[-1] = nom.replace(u, uuid_cible)
                    trouves.append(("/".join(relatif), tar.extractfile(membre).read(), u))
                    break
    print()
    return trouves, fichiers_joueurs


def resume_joueur(contenu):
    """Affiche le contenu du fichier joueur, renvoie le nombre d'objets (ou None)."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dat")
    try:
        tmp.write(contenu)
        tmp.close()
        joueur = racine(nbtlib.load(tmp.name))
        inventaire = joueur.get("Inventory", [])
        equipement = joueur.get("equipment", {})
        ender = joueur.get("EnderItems", [])
        print(f"    inventaire : {len(inventaire)} emplacements")
        print(f"    équipement : {len(equipement)}")
        print(f"    ender chest : {len(ender)}")
        if "Pos" in joueur:
            x, y, z = (round(float(v)) for v in joueur["Pos"])
            print(f"    position : {x} {y} {z} ({joueur.get('Dimension', '?')})")
        return len(inventaire) + len(equipement) + len(ender)
    except Exception as e:
        print(f"    (illisible : {e})")
        return None
    finally:
        tmp.close()
        os.unlink(tmp.name)


def main():
    ap = argparse.ArgumentParser(description="Restaure un joueur depuis une sauvegarde de serveur.")
    ap.add_argument("--archive", required=True, help="sauvegarde .tar.gz du serveur")
    ap.add_argument("--monde-nom", required=True, help="nom du dossier du monde dans l'archive")
    ap.add_argument("--pseudo", required=True)
    ap.add_argument("--uuid", help="UUID du joueur (sinon demandé à Mojang)")
    ap.add_argument("--monde", help="chemin du monde solo (sinon cherché tout seul)")
    args = ap.parse_args()

    archive = Path(args.archive)
    if not archive.exists():
        sys.exit(f"Archive introuvable : {archive}")
    uid = args.uuid or uuid_mojang(args.pseudo)
    if not uid:
        sys.exit("Impossible de trouver l'UUID, passe-le avec --uuid.")
    monde = trouver_monde(args.monde_nom, args.monde)
    print(f"Monde : {monde}\nJoueur : {args.pseudo} ({uid})\n")

    trouves, fichiers_joueurs = lire_archive(
        archive, args.monde_nom, [uid, uuid_hors_ligne(args.pseudo)], uid)

    if not trouves:
        print(f"Rien trouvé pour {args.pseudo} dans {args.monde_nom}.")
        if fichiers_joueurs:
            print("Fichiers joueurs présents dans l'archive :")
            for f in fichiers_joueurs[:30]:
                print("  ", f)
        sys.exit(1)

    print("Trouvé :")
    analyse, total = False, 0
    for relatif, contenu, u in trouves:
        note = "" if u == uid else "  (UUID hors-ligne, renommé)"
        print(f"  {relatif}  ({len(contenu) // 1024 + 1} Ko){note}")
        if relatif.endswith(".dat"):
            n = resume_joueur(contenu)
            if n is not None:
                analyse, total = True, total + n
    if analyse and total == 0:
        print("\nLe fichier ne contient aucun objet (mort ou mains vides à la fermeture ?)."
              " La position sera quand même restaurée.")

    input("\nFerme Minecraft puis appuie sur Entrée (Ctrl+C pour annuler)...")

    sauvegarde = Path.cwd() / f"sauvegarde_{monde.name}_{datetime.now():%Y%m%d_%H%M%S}"
    sauvegarde.mkdir(parents=True)
    shutil.copy2(monde / "level.dat", sauvegarde / "level.dat")
    for dossier in ("players", "playerdata"):
        if (monde / dossier).exists():
            shutil.copytree(monde / dossier, sauvegarde / dossier)
    print(f"Copie de sécurité : {sauvegarde}")

    for relatif, contenu, _ in trouves:
        cible = monde / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(contenu)
        print(f"Restauré : {relatif}")

    # en solo, le joueur de level.dat passe avant playerdata : on l'enlève
    level = nbtlib.load(monde / "level.dat")
    data = racine(level)["Data"]
    if "Player" in data:
        del data["Player"]
        level.save()
        print("Joueur vide retiré de level.dat.")

    print("\nFini, tu peux relancer le monde.")


if __name__ == "__main__":
    main()
