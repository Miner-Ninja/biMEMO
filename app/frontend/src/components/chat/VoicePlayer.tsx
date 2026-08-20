import { useState, useRef, useEffect, useCallback } from "react";
import { Play, Pause, Mic } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const BAR_COUNT = 50;
const BAR_WIDTH = 2;
const BAR_GAP = 1.5;

interface VoicePlayerProps {
  src: string;
  className?: string;
}

function toBlobUrl(dataUrl: string): string {
  const commaIdx = dataUrl.indexOf(",");
  const header = dataUrl.slice(0, commaIdx);
  const b64 = dataUrl.slice(commaIdx + 1);
  const mime = header.match(/:(.*?);/)?.[1] ?? "audio/webm";
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: mime }));
}

function resolveCssColor(varName: string, fallback: string): string {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(varName)
    .trim();
  if (!raw) return fallback;
  if (/^\d/.test(raw)) return `hsl(${raw})`;
  return raw;
}

function fmt(s: number) {
  if (!s || !isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export function VoicePlayer({ src, className }: VoicePlayerProps) {
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [displayTime, setDisplayTime] = useState("0:00");
  const [waveformData, setWaveformData] = useState<number[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const durRef = useRef(0);
  const colorsRef = useRef({ active: "#a855f7", inactive: "#9ca3af" });

  // Resolve CSS colors on mount + theme change
  useEffect(() => {
    const resolve = () => {
      colorsRef.current = {
        active: resolveCssColor("--primary", "#a855f7"),
        inactive: resolveCssColor("--muted-foreground", "#9ca3af"),
      };
    };
    resolve();
    const observer = new MutationObserver(resolve);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);

  // Draw waveform bars on canvas
  const drawCanvas = useCallback(
    (prog: number) => {
      const canvas = canvasRef.current;
      if (!canvas || waveformData.length === 0) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const { active, inactive } = colorsRef.current;
      const totalBarWidth = BAR_WIDTH + BAR_GAP;
      const startX = (w - BAR_COUNT * totalBarWidth + BAR_GAP) / 2;
      const progressIdx = prog > 0 ? Math.floor(prog * BAR_COUNT) : -1;

      for (let i = 0; i < BAR_COUNT; i++) {
        const amplitude = waveformData[i] ?? 0.3;
        const barHeight = Math.max(3, amplitude * h * 0.8);
        const x = startX + i * totalBarWidth;
        const y = (h - barHeight) / 2;

        if (progressIdx >= 0 && i <= progressIdx) {
          ctx.globalAlpha = 1;
          ctx.fillStyle = active;
        } else {
          ctx.globalAlpha = 0.55;
          ctx.fillStyle = inactive;
        }

        ctx.beginPath();
        ctx.roundRect(x, y, BAR_WIDTH, barHeight, 1);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    },
    [waveformData]
  );

  // Redraw when waveform loads or progress changes
  useEffect(() => {
    drawCanvas(progress);
  }, [drawCanvas, progress]);

  // Init audio + decode waveform
  useEffect(() => {
    const url = src.startsWith("data:") ? toBlobUrl(src) : src;
    if (src.startsWith("data:")) blobUrlRef.current = url;

    const audio = new Audio(url);
    audio.preload = "auto";
    audioRef.current = audio;

    const setDur = (d: number) => {
      if (d > 0 && isFinite(d)) {
        durRef.current = d;
        setDisplayTime(fmt(d));
      }
    };

    audio.addEventListener("loadedmetadata", () => setDur(audio.duration));
    audio.addEventListener("durationchange", () => setDur(audio.duration));

    audio.addEventListener("timeupdate", () => {
      const d = durRef.current;
      if (d > 0) {
        setProgress(audio.currentTime / d);
        setDisplayTime(fmt(d - audio.currentTime));
      }
    });

    audio.addEventListener("ended", () => {
      setPlaying(false);
      setProgress(0);
      setDisplayTime(fmt(durRef.current));
    });

    // Decode waveform peaks + get accurate duration
    const actx = new AudioContext();
    fetch(url)
      .then((r) => r.arrayBuffer())
      .then((buf) => actx.decodeAudioData(buf))
      .then((decoded) => {
        setDur(decoded.duration);
        const raw = decoded.getChannelData(0);
        const step = Math.floor(raw.length / BAR_COUNT);
        const peaks: number[] = [];
        for (let i = 0; i < BAR_COUNT; i++) {
          let max = 0;
          for (let j = 0; j < step; j++) {
            const abs = Math.abs(raw[i * step + j] ?? 0);
            if (abs > max) max = abs;
          }
          peaks.push(max);
        }
        const maxPeak = Math.max(...peaks, 0.01);
        setWaveformData(peaks.map((p) => p / maxPeak));
      })
      .catch(() => {
        setWaveformData(
          Array.from(
            { length: BAR_COUNT },
            (_, i) => 0.3 + 0.4 * Math.sin(i * 0.5) + 0.3 * Math.sin(i * 1.3)
          )
        );
      })
      .finally(() => actx.close().catch(() => {}));

    return () => {
      audio.pause();
      audio.src = "";
      audioRef.current = null;
      durRef.current = 0;
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [src]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playing) {
      audio.pause();
      setPlaying(false);
    } else {
      audio
        .play()
        .then(() => setPlaying(true))
        .catch((err) => console.error("Playback failed:", err));
    }
  };

  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const audio = audioRef.current;
    const canvas = canvasRef.current;
    const d = durRef.current;
    if (!audio || !canvas || d <= 0) return;
    const rect = canvas.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    audio.currentTime = pct * d;
  };

  const canvasWidth = BAR_COUNT * (BAR_WIDTH + BAR_GAP);

  return (
    <Card className={cn("inline-flex shadow-none", className)}>
      <CardContent className="inline-flex items-center gap-2 px-3 py-2">
        <Button
          type="button"
          onClick={togglePlay}
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0 rounded-full"
        >
          {playing ? (
            <Pause className="h-3.5 w-3.5" />
          ) : (
            <Play className="h-3.5 w-3.5 ml-0.5" />
          )}
        </Button>

        <canvas
          ref={canvasRef}
          width={canvasWidth}
          height={32}
          className="h-8 cursor-pointer"
          onClick={handleCanvasClick}
        />

        <span className="text-xs font-mono tabular-nums text-muted-foreground min-w-[2.5rem] text-right">
          {displayTime}
        </span>

        <Badge variant="secondary" className="shrink-0 gap-1">
          <Mic className="h-3.5 w-3.5" />
        </Badge>
      </CardContent>
    </Card>
  );
}
