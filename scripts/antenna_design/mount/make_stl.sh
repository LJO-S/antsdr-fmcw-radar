#!/usr/bin/env bash
# Export every k9_mount.scad part to ../local/mount/<part>.stl (git-ignored).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out="$here/../local/mount"
mkdir -p "$out"
for p in bracket_tx bracket_rx clamp_20 clamp_35 mast_head mast_base pole_top; do
  openscad -q -D "part=\"$p\"" -o "$out/$p.stl" "$here/k9_mount.scad"
  echo "$out/$p.stl"
done
