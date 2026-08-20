import { useState, useRef, useCallback, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Mic, Square, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const MAX_RECORDING_SECONDS = 120;
const BAR_COUNT = 40;
const BAR_GAP = 2;
const BAR_WIDTH = 3;
const FALLBACK_WAVEFORM_COLOR = "hsl(0 72% 51%)";

interface VoiceRecorderProps {
  onRecorded: (voiceDataUrl: string) => void;
  onRecordingChange?: (recording: boolean) => void;
  disabled?: boolean;
  className?: string;
  autoStart?: boolean;
  embedded?: boolean;
}

/**
 * Microphone recording button with live waveform visualization.
 * Uses Web Audio API AnalyserNode for real-time audio levels.
 * Records audio/webm, encodes as base64 data URL.
 * Hidden if navigator.mediaDevices is unavailable.
 */
export function VoiceRecorder({
  onRecorded,
  onRecordingChange,
  disabled = false,
  className,
  autoStart = false,
  embedded = false,
}: VoiceRecorderProps) {
  const { t } = useTranslation();
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [supported, setSupported] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const animFrameRef = useRef<number>(0);

  useEffect(() => {
    setSupported(
      typeof navigator !== "undefined" &&
        !!navigator.mediaDevices &&
        typeof navigator.mediaDevices.getUserMedia === "function"
    );
  }, []);

  const getWaveformColor = useCallback(() => {
    if (typeof window === "undefined") {
      return FALLBACK_WAVEFORM_COLOR;
    }

    const destructiveColor = window
      .getComputedStyle(document.documentElement)
      .getPropertyValue("--destructive")
      .trim();

    if (!destructiveColor) {
      return FALLBACK_WAVEFORM_COLOR;
    }

    return `oklch(${destructiveColor})`;
  }, []);

  const drawWaveform = useCallback(
    function drawWaveform() {
      const analyser = analyserRef.current;
      const canvas = canvasRef.current;
      if (!analyser || !canvas) return;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      analyser.getByteFrequencyData(dataArray);

      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const totalBarWidth = BAR_WIDTH + BAR_GAP;
      const startX = (w - BAR_COUNT * totalBarWidth + BAR_GAP) / 2;
      const step = Math.floor(bufferLength / BAR_COUNT);

      for (let i = 0; i < BAR_COUNT; i++) {
        // Average a slice of frequency data for this bar
        let sum = 0;
        for (let j = 0; j < step; j++) {
          sum += dataArray[i * step + j] ?? 0;
        }
        const avg = sum / step / 255;

        const barHeight = Math.max(3, avg * h * 0.85);
        const x = startX + i * totalBarWidth;
        const y = (h - barHeight) / 2;

        ctx.fillStyle = getWaveformColor();
        ctx.beginPath();
        ctx.roundRect(x, y, BAR_WIDTH, barHeight, 1.5);
        ctx.fill();
      }

      animFrameRef.current = requestAnimationFrame(drawWaveform);
    },
    [getWaveformColor]
  );

  const cleanup = useCallback(() => {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = 0;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const stopRecording = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
    cleanup();
    setRecording(false);
    setSeconds(0);
    onRecordingChange?.(false);
  }, [cleanup, onRecordingChange]);

  const cancelRecording = useCallback(() => {
    chunksRef.current = [];
    stopRecording();
  }, [stopRecording]);

  // Cancel on Escape
  useEffect(() => {
    if (!recording) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancelRecording();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [recording, cancelRecording]);

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Set up Web Audio API for visualization
      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.7;
      source.connect(analyser);
      analyserRef.current = analyser;

      const recorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : "audio/webm",
      });
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        if (chunksRef.current.length === 0) return;
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        chunksRef.current = [];
        const reader = new FileReader();
        reader.onload = () => onRecorded(reader.result as string);
        reader.readAsDataURL(blob);
      };

      recorder.start();
      setRecording(true);
      setSeconds(0);
      onRecordingChange?.(true);

      // Start waveform animation
      animFrameRef.current = requestAnimationFrame(drawWaveform);

      // Timer
      timerRef.current = setInterval(() => {
        setSeconds((prev) => {
          const next = prev + 1;
          if (next >= MAX_RECORDING_SECONDS) stopRecording();
          return next;
        });
      }, 1000);
    } catch (err) {
      console.error("Microphone access denied:", err);
    }
  }, [drawWaveform, onRecorded, onRecordingChange, stopRecording]);

  const handleClick = () => {
    if (recording) {
      stopRecording();
    } else {
      void startRecording();
    }
  };

  useEffect(() => {
    if (!autoStart || disabled || !supported || recording) {
      return;
    }
    void startRecording();
  }, [autoStart, disabled, recording, startRecording, supported]);

  if (!supported) return null;

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  if (recording) {
    return (
      <Card
        className={cn(
          embedded
            ? "border-0 bg-transparent shadow-none"
            : "border-destructive/30 bg-destructive/5 shadow-none",
          className
        )}
      >
        <CardContent className="flex items-center gap-2 px-3 py-1.5">
          <Badge variant="danger" className="gap-1.5">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-destructive opacity-75" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-destructive" />
            </span>
            <span className="font-mono text-xs tabular-nums font-medium">
              {formatTime(seconds)}
            </span>
          </Badge>

          <canvas
            ref={canvasRef}
            width={BAR_COUNT * (BAR_WIDTH + BAR_GAP)}
            height={32}
            className="h-8 flex-1"
          />

          <Button
            type="button"
            onClick={cancelRecording}
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0 text-muted-foreground hover:text-foreground"
            aria-label={t("chat.cancelRecording")}
          >
            <X className="h-4 w-4" />
          </Button>

          <Button
            type="button"
            onClick={stopRecording}
            variant="destructive"
            size="icon"
            className="h-9 w-9 shrink-0 rounded-full"
            aria-label={t("chat.stopAndSend")}
          >
            <Square className="h-4 w-4" />
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      variant="ghost"
      size="icon"
      className={cn("h-11 w-11 shrink-0", className)}
      aria-label={t("chat.recordVoiceMessage")}
    >
      <Mic className="h-5 w-5" />
    </Button>
  );
}
