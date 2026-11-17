#!/bin/sh
set -eu
cd "$(dirname "$0")"
figure=${1:-cabinet-storage}
case "$figure" in cabinet-storage|cabinet-video-sequence|cabinet-attestation-sequence|cabinet-checkpoint-sequence|cabinet-migration-sequence) ;; *) exit 2 ;; esac
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
xelatex -halt-on-error -interaction=batchmode -output-directory="$scratch" "$figure.tex"
gs -q -dBATCH -dNOPAUSE -sDEVICE=pdfwrite -dNoOutputFonts -sOutputFile="$scratch/outlined.pdf" "$scratch/$figure.pdf"
pdftoppm -r 360 -png -singlefile "$scratch/outlined.pdf" "$scratch/$figure"
cp "$scratch/outlined.pdf" "$figure.pdf"
cp "$scratch/$figure.png" "$figure.png"
