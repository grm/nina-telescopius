#!/usr/bin/env bash
# Downloads the unmodified NINA sources exercised by the compatibility harness
# (MPL-2.0, https://bitbucket.org/Isbeorn/nina) into ./vendor.
set -euo pipefail
NINA_REF="${NINA_REF:-7c9de0c4202f2d054f58b0cb6b15d561f8b6b4b2}"
BASE="https://bitbucket.org/Isbeorn/nina/raw/${NINA_REF}"
FILES=(
  NINA.Equipment/Equipment/MyPlanetarium/Stellarium.cs
  NINA.Equipment/Interfaces/IPlanetarium.cs
  NINA.Equipment/Exceptions/PlanetariumFailedToConnect.cs
  NINA.Equipment/Exceptions/PlanetariumObjectNotSelectedException.cs
  NINA.Core/Utility/Http/HttpRequest.cs
  NINA.Core/Utility/Http/HttpGetRequest.cs
  NINA.Astrometry/Angle.cs
)
cd "$(dirname "$0")"
mkdir -p vendor
for f in "${FILES[@]}"; do
  curl -fsSL "${BASE}/${f}" -o "vendor/$(basename "$f")"
done
echo "NINA sources @ ${NINA_REF} -> $(pwd)/vendor"
