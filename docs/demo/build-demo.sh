#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${TMPDIR:-/tmp}/standard-astro-demo-build"
SILENT_VIDEO="$BUILD_DIR/standard-astro-claim-audit-demo-silent.mp4"
RAW_AUDIO="$BUILD_DIR/standard-astro-demo-audio.m4a"
NORMALIZED_AUDIO="$BUILD_DIR/standard-astro-demo-audio-normalized.m4a"
OUTPUT="$ROOT/standard-astro-claim-audit-demo.mp4"

mkdir -p "$BUILD_DIR"
cd "$ROOT"

for frame in title input status gap verified end; do
  test -f "frames/$frame.png"
done

ffmpeg -hide_banner -loglevel error -y \
  -loop 1 -framerate 30 -t 4.5 -i frames/title.png \
  -loop 1 -framerate 30 -t 5.5 -i frames/input.png \
  -loop 1 -framerate 30 -t 7.0 -i frames/status.png \
  -loop 1 -framerate 30 -t 7.0 -i frames/gap.png \
  -loop 1 -framerate 30 -t 6.5 -i frames/verified.png \
  -loop 1 -framerate 30 -t 4.5 -i frames/end.png \
  -filter_complex "\
[0:v]scale=1920:1080,zoompan=z='min(max(zoom,pzoom)+0.00008,1.02)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,settb=AVTB,setpts=PTS-STARTPTS[v0];\
[1:v]scale=1920:1080,zoompan=z='min(max(zoom,pzoom)+0.00009,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,settb=AVTB,setpts=PTS-STARTPTS[v1];\
[2:v]scale=1920:1080,zoompan=z='min(max(zoom,pzoom)+0.00007,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,settb=AVTB,setpts=PTS-STARTPTS[v2];\
[3:v]scale=1920:1080,zoompan=z='min(max(zoom,pzoom)+0.00007,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,settb=AVTB,setpts=PTS-STARTPTS[v3];\
[4:v]scale=1920:1080,zoompan=z='min(max(zoom,pzoom)+0.00008,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,settb=AVTB,setpts=PTS-STARTPTS[v4];\
[5:v]scale=1920:1080,zoompan=z='min(max(zoom,pzoom)+0.00006,1.018)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,settb=AVTB,setpts=PTS-STARTPTS[v5];\
[v0][v1]xfade=transition=fade:duration=0.6:offset=3.9[x1];\
[x1][v2]xfade=transition=fade:duration=0.6:offset=8.8[x2];\
[x2][v3]xfade=transition=fade:duration=0.6:offset=15.2[x3];\
[x3][v4]xfade=transition=fade:duration=0.6:offset=21.6[x4];\
[x4][v5]xfade=transition=fade:duration=0.6:offset=27.5,fade=t=in:st=0:d=0.25,fade=t=out:st=31.3:d=0.7[vout]" \
  -map "[vout]" -t 32 -r 30 -an \
  -c:v libx264 -crf 18 -preset medium -profile:v high -level 4.0 \
  -pix_fmt yuv420p -movflags +faststart "$SILENT_VIDEO"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi \
  -i "aevalsrc=exprs='0.024*(sin(2*PI*110*t)+0.62*sin(2*PI*164.81*t)+0.38*sin(2*PI*220*t))*(0.72+0.18*sin(2*PI*0.055*t))|0.024*(sin(2*PI*110*t+0.08)+0.62*sin(2*PI*164.81*t+0.05)+0.38*sin(2*PI*220*t+0.12))*(0.72+0.18*sin(2*PI*0.055*t+0.4))':s=48000:d=32" \
  -f lavfi \
  -i "aevalsrc=exprs='0.22*sin(2*PI*1450*(t-3.9))*exp(-34*(t-3.9))*between(t,3.9,4.15)+0.13*sin(2*PI*1046.5*(t-8.8))*exp(-7*(t-8.8))*between(t,8.8,9.35)+0.13*sin(2*PI*1318.5*(t-9.0))*exp(-7*(t-9.0))*between(t,9.0,9.55)+0.16*sin(2*PI*1120*(t-15.2))*exp(-15*(t-15.2))*between(t,15.2,15.55)+0.12*sin(2*PI*1174.7*(t-21.6))*exp(-6*(t-21.6))*between(t,21.6,22.2)+0.12*sin(2*PI*1568*(t-21.82))*exp(-6*(t-21.82))*between(t,21.82,22.42)+0.10*sin(2*PI*2093*(t-22.04))*exp(-6*(t-22.04))*between(t,22.04,22.64)+0.10*sin(2*PI*1320*(t-27.5))*exp(-5*(t-27.5))*between(t,27.5,28.35)|0.22*sin(2*PI*1450*(t-3.9))*exp(-34*(t-3.9))*between(t,3.9,4.15)+0.13*sin(2*PI*1046.5*(t-8.8))*exp(-7*(t-8.8))*between(t,8.8,9.35)+0.13*sin(2*PI*1318.5*(t-9.0))*exp(-7*(t-9.0))*between(t,9.0,9.55)+0.16*sin(2*PI*1120*(t-15.2))*exp(-15*(t-15.2))*between(t,15.2,15.55)+0.12*sin(2*PI*1174.7*(t-21.6))*exp(-6*(t-21.6))*between(t,21.6,22.2)+0.12*sin(2*PI*1568*(t-21.82))*exp(-6*(t-21.82))*between(t,21.82,22.42)+0.10*sin(2*PI*2093*(t-22.04))*exp(-6*(t-22.04))*between(t,22.04,22.64)+0.10*sin(2*PI*1320*(t-27.5))*exp(-5*(t-27.5))*between(t,27.5,28.35)':s=48000:d=32" \
  -filter_complex "\
[0:a]lowpass=f=4000,volume=0.45,afade=t=in:st=0:d=0.3,afade=t=out:st=30.5:d=1.5[bgm];\
[1:a]highpass=f=800,volume=0.90[sfx];\
[bgm][sfx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.92[a]" \
  -map "[a]" -c:a aac -b:a 192k "$RAW_AUDIO"

ffmpeg -hide_banner -loglevel error -y \
  -i "$RAW_AUDIO" \
  -af "loudnorm=I=-22:TP=-2:LRA=7,aresample=48000" \
  -ar 48000 -c:a aac -b:a 192k "$NORMALIZED_AUDIO"

ffmpeg -hide_banner -loglevel error -y \
  -i "$SILENT_VIDEO" -i "$NORMALIZED_AUDIO" \
  -map 0:v:0 -map 1:a:0 -c:v copy -c:a copy -shortest \
  -movflags +faststart "$OUTPUT"

ffprobe -v error \
  -show_entries format=duration,size \
  -show_entries stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels \
  -of json "$OUTPUT"
