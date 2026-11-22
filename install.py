#!/usr/bin/env python3
"""Install only the relocatable rctl symlink; never alter shell or WM config."""
import argparse
from pathlib import Path
import os

parser = argparse.ArgumentParser()
parser.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin')
args = parser.parse_args()
source = Path(__file__).resolve().parent / 'bin/rctl'
try:
    args.bin_dir.mkdir(parents=True, exist_ok=True)
    target = args.bin_dir / 'rctl'
    if target.is_symlink() and target.resolve() == source:
        print(f'Already installed: {target}')
    elif target.exists() or target.is_symlink():
        raise RuntimeError(f'Refusing to overwrite existing command: {target}')
    else:
        target.symlink_to(os.path.relpath(source, args.bin_dir.resolve()))
        print(f'Installed: {target}')
except (OSError, RuntimeError) as exc:
    parser.exit(1, f'Installation failed: {exc}\n')
