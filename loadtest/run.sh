#!/bin/bash
# usage: run.sh <script> <label> [ENV=VAL ...]; k6 pinned to cores 6,7, summary JSON to results/<label>.json
S=$1; L=$2; shift 2; E=(); for kv in "$@"; do E+=(-e "$kv"); done
mkdir -p results
docker run --rm --user $(id -u):$(id -g) --network host --cpuset-cpus 6,7 --ulimit nofile=1048576:1048576 -v "$PWD":/w -w /w "${E[@]}" \
  grafana/k6:0.54.0 run --quiet --no-color --summary-export results/$L.json $S 2>&1 | tail -n 60 > results/$L.txt
