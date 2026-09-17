#!/usr/bin/env bash
# Launch from any cwd with the project's vLLM environment; no paid endpoint.
set -u
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1
CURATION_PYTHON="${CURATION_PYTHON:-$PWD/../env/bin/python}"
CURATION_RUN="$PWD/state/curation/image_preannotation_v1"
exec 9>"$CURATION_RUN/launcher.lock"
flock -n 9 || exit 1
trap 'touch "$CURATION_RUN/STOP"' TERM INT
while [[ ! -e "$CURATION_RUN/STOP" ]]; do
    "$CURATION_PYTHON" -u -m curation.image_supervisor --run "$CURATION_RUN" --concurrency 12
    CURATION_EXIT=$?
    # Normal exit means completion or explicit pause. Only crashes are retried.
    [[ "$CURATION_EXIT" -eq 0 ]] && exit 0
    [[ -e "$CURATION_RUN/STOP" ]] && exit 0
    sleep 30 & wait $!
done
