#!/bin/sh
# Run once while online. The charts then work with no internet at the venue.
cd "$(dirname "$0")/static/vendor" || exit 1
curl -L -o chart.umd.min.js https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js
ls -la
