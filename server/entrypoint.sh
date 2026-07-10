#!/bin/sh
set -e
# Corrige ownership dos diretórios montados como volumes antes de largar root
for d in uploads auth erp_db integrador replicacao setup; do
    target="/opt/pdv-server/$d"
    [ -d "$target" ] && chown -R pdv:pdv "$target" 2>/dev/null || true
done
exec gosu pdv "$@"
