#!/usr/bin/env python3
"""Start an isolated local Alexis development surface or its shared brain.

Configuration is private and outside the checkout. The brain URL is a deployment
setting, so neither the page nor conversation engine depends on this host.
"""
import argparse
import os
from pathlib import Path

import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument("service", choices=["brain", "web"])
parser.add_argument("--config-dir", type=Path, default=Path.home() / ".brutus/alexis-development")
args = parser.parse_args()
root = args.config_dir.expanduser()
if args.service == "brain":
    os.environ.setdefault("ALEXIS_DB", str(root / "brain.sqlite"))
    os.environ.setdefault("ALEXIS_SURFACE_TOKENS_FILE", str(root / "surfaces.json"))
    # Development-only choice. Cloud deployments set an explicit API provider.
    os.environ.setdefault("ALEXIS_MODEL_PROVIDER", "claude-cli")
    from brutus.alexis_brain import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=8794)
else:
    os.environ["BRUTUS_CONFIG"] = str(root / "config.yaml")
    os.environ["BRUTUS_STATE_DIR"] = str(root / "state")
    from brutus.config import load_config
    from brutus.server import create_app
    uvicorn.run(create_app(load_config(), start_watchdog=False), host="127.0.0.1", port=8789)
