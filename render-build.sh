#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install Python requirements
pip install -r requirements.txt

# Create custom bin directory
mkdir -p bin

# Download pre-built static FFmpeg binary (No root needed)
curl -L -o ffmpeg.tar.xz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xf ffmpeg.tar.xz --strip-components 1 -C bin
rm ffmpeg.tar.xz

# Add binary to system path
export PATH="$PWD/bin:$PATH"