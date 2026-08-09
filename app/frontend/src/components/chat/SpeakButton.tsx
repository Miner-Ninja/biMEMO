import { useState, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Volume2, Loader2, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { synthesizeSpeech } from "@/lib/bimemo-api";

interface SpeakButtonProps {
  text: string;
}

export function SpeakButton({ text }: SpeakButtonProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.removeAttribute("src");
      audioRef.current = null;
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  const handleClick = async () => {
    if (state === "playing") {
      cleanup();
      setState("idle");
      return;
    }

    if (state === "loading") return;

    setState("loading");
    try {
      const blobUrl = await synthesizeSpeech(text);
      blobUrlRef.current = blobUrl;

      const audio = new Audio(blobUrl);
      audioRef.current = audio;

      audio.addEventListener("ended", () => {
        cleanup();
        setState("idle");
      });

      audio.addEventListener("error", () => {
        cleanup();
        setState("idle");
      });

      await audio.play();
      setState("playing");
    } catch {
      cleanup();
      setState("idle");
    }
  };

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={handleClick}
      className="h-7 w-7"
      aria-label={
        state === "loading"
          ? t("chat.synthesizing")
          : state === "playing"
            ? t("chat.stopPlayback")
            : t("chat.readAloud")
      }
    >
      {state === "loading" ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : state === "playing" ? (
        <Square className="h-3.5 w-3.5" />
      ) : (
        <Volume2 className="h-3.5 w-3.5" />
      )}
    </Button>
  );
}
