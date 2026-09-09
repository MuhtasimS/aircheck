# Northstar Broadcast Master

Fictional destination specification for **The Last Lightkeeper**.

## 1. Program media

The program master MUST be a QuickTime MOV with H.264 video at 1920 by 1080
pixels and a frame rate of 24000/1001 fps.

## 2. Program audio

The program master MUST contain exactly two stereo audio channels at 48000 Hz.
Integrated loudness MUST be -24 LUFS plus or minus 2 LU. True peak MUST NOT
exceed -2 dBTP.

## 3. Captions

English captions MUST be supplied as a WebVTT sidecar.

## 4. Filename

The program master filename MUST exactly equal
`THE_LAST_LIGHTKEEPER_NSBM_v1.mov`.

## 5. Package integrity

The delivery MUST include a SHA-256 checksum manifest covering every delivered
asset.
