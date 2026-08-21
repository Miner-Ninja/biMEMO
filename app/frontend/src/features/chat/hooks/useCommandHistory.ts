import { useState, type KeyboardEvent } from "react";

interface UseCommandHistoryParams {
  commandHistory: string[];
  value: string;
  setValue: (value: string) => void;
}

export function useCommandHistory({
  commandHistory,
  value,
  setValue,
}: UseCommandHistoryParams) {
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [savedInput, setSavedInput] = useState("");

  const handleHistoryKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (e.key === "ArrowUp" && commandHistory.length > 0) {
      const isEmpty = value.trim() === "";
      const isBrowsing = historyIndex >= 0;
      if (isEmpty || isBrowsing) {
        e.preventDefault();
        if (!isBrowsing) {
          setSavedInput(value);
        }
        const nextIndex = Math.min(historyIndex + 1, commandHistory.length - 1);
        setHistoryIndex(nextIndex);
        setValue(commandHistory[nextIndex] ?? value);
        return true;
      }
    }
    if (e.key === "ArrowDown" && historyIndex >= 0) {
      e.preventDefault();
      const nextIndex = historyIndex - 1;
      if (nextIndex < 0) {
        setHistoryIndex(-1);
        setValue(savedInput);
      } else {
        setHistoryIndex(nextIndex);
        setValue(commandHistory[nextIndex] ?? savedInput);
      }
      return true;
    }

    return false;
  };

  const handleHistoryInputChange = () => {
    if (historyIndex >= 0) {
      setHistoryIndex(-1);
      setSavedInput("");
    }
  };

  const resetHistory = () => {
    setHistoryIndex(-1);
    setSavedInput("");
  };

  return {
    historyIndex,
    handleHistoryKeyDown,
    handleHistoryInputChange,
    resetHistory,
  };
}
