#!/bin/bash
# Double-click this in Finder to run push_to_droplet.sh from a fresh
# Terminal window, without needing to open a terminal or cd anywhere
# yourself. See push_to_droplet.sh's own comments for setup + what it does.
cd "$(dirname "$0")"
./push_to_droplet.sh
echo
read -r -p "Done -- press Enter to close this window..."
