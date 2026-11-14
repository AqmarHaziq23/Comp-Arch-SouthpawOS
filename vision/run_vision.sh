#!/usr/bin/env bash
# run_vision.sh - Southpaw OS, computer vision (Member D)
# Starts the live vision process with one command.
#
#   ./run_vision.sh             no window (over SSH, and on demo day)
#   ./run_vision.sh --window    with the overlay window (on the Pi desktop via Raspberry Pi Connect)
#   ./run_vision.sh --source recordings/test_left/video.avi    replay a clip instead of the webcam
#
# Any other options are passed straight to vision_main.py.
# First time only: chmod +x run_vision.sh

cd "$(dirname "$0")" || exit 1          # always run from the vision folder
source .venv/bin/activate || { echo "No .venv here - see README.md setup"; exit 1; }

ARGS="--no-display"
if [ "$1" = "--window" ]; then
    ARGS=""
    shift
fi

# nice -n 5 = slightly lower CPU priority than the hub, so Bluetooth and the game logic
# always get the CPU first when the Pi is busy (vision is only a second opinion)
exec nice -n 5 python vision_main.py $ARGS "$@"
