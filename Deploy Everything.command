#!/bin/bash
# Double-click this in Finder to run deploy_all.sh from a fresh Terminal
# window, without needing to open a terminal or cd anywhere yourself.
# See deploy_all.sh's own comments for setup + what it does.
cd "$(dirname "$0")"
./deploy_all.sh
echo
read -r -p "Done -- press Enter to close this window..."
