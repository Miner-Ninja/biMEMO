import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

const UUID_TEMPLATE = "10000000-1000-4000-8000-100000000000";
const UUID_HEX_RADIX = 16;
const UUID_RANDOM_MASK = 15;
const UUID_VERSION_MASK = 3;

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function generateId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }

  if (typeof globalThis.crypto?.getRandomValues === "function") {
    const randomBytes = new Uint8Array(UUID_TEMPLATE.length);
    globalThis.crypto.getRandomValues(randomBytes);

    let randomIndex = 0;

    return UUID_TEMPLATE.replace(/[018]/g, (character) => {
      const randomValue = (randomBytes[randomIndex] ?? 0) & UUID_RANDOM_MASK;
      randomIndex += 1;
      const characterCode = Number(character);
      const maskedValue =
        characterCode ^ (randomValue & (UUID_VERSION_MASK >> (characterCode / 4)));
      return maskedValue.toString(UUID_HEX_RADIX);
    });
  }

  return `${Date.now()}-${Math.random().toString(UUID_HEX_RADIX).slice(2)}`;
}

export function formatDate(date: Date): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function truncate(str: string, maxLength: number): string {
  if (str.length <= maxLength) return str;
  return str.slice(0, maxLength - 3) + "...";
}
