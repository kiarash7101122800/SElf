#!/bin/sh
set -eu
umask 077
mkdir -p data/action downloads logs sessions
exec python -u app.py
