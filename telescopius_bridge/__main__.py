"""Entry point: python -m telescopius_bridge [--config config.json] [--port 8090]"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .config import ConfigError, Store
from .server import App, create_servers, serve

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="telescopius_bridge", description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"fichier de configuration (défaut : {DEFAULT_CONFIG})")
    parser.add_argument("--host", help="interface d'écoute (défaut : config, sinon localhost)")
    parser.add_argument("--port", type=int, help="port (défaut : config, sinon 8090)")
    parser.add_argument("--open", action="store_true", help="ouvrir l'interface dans le navigateur")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # show messages promptly when piped

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        store = Store(args.config)
    except (ConfigError, ValueError) as e:
        print(f"Configuration invalide ({args.config}) : {e}", file=sys.stderr)
        return 2

    config = store.config
    host = args.host or config["host"]
    port = args.port if args.port is not None else config["port"]
    try:
        servers = create_servers(App(store), host, port)
    except OSError as e:
        print(f"Impossible d'écouter sur {host}:{port} ({e}).\n"
              f"Stellarium tourne peut-être déjà sur ce port : ferme-le ou choisis un autre "
              f"port (--port) et reporte-le dans NINA.", file=sys.stderr)
        return 1

    serve(servers)
    port = servers[0].server_address[1]
    ui_host = "localhost" if host in ("localhost", "0.0.0.0", "::", "*") else host
    url = f"http://{ui_host}:{port}/"
    print(f"Telescopius bridge {__version__} — interface : {url}")
    print(f"Dans NINA : Options > Équipement > Planétarium > Stellarium, hôte "
          f"{'localhost' if host == 'localhost' else '<ip de cette machine>'} port {port}")
    if not config["api_key"]:
        print("Attention : aucune clé API, renseigne-la dans l'interface (Réglages).")
    if not store.has_location():
        print("Attention : position non configurée, renseigne-la dans l'interface (Réglages).")
    if args.open:
        webbrowser.open(url)

    try:
        while True:  # short sleeps so Ctrl+C is honoured on Windows too
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        for srv in servers:
            srv.shutdown()
            srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
