import { useState, type RefObject, type KeyboardEvent } from "react";

interface UseFileMentionParams {
  value: string;
  setValue: (value: string) => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  availableFiles?: string[];
}

export function useFileMention({
  value,
  setValue,
  textareaRef,
  availableFiles,
}: UseFileMentionParams) {
  const [showFileMention, setShowFileMention] = useState(false);
  const [filteredFiles, setFilteredFiles] = useState<string[]>([]);
  const [fileSelectedIndex, setFileSelectedIndex] = useState(0);
  const [mentionStart, setMentionStart] = useState(-1);

  const handleFileMentionSelect = (filename: string) => {
    const before = value.slice(0, mentionStart);
    const after = value.slice(textareaRef.current?.selectionStart ?? value.length);
    const newValue = `${before}@${filename} ${after}`;
    setValue(newValue);
    setShowFileMention(false);
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        const cursorPos = before.length + filename.length + 2; // @filename + space
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(cursorPos, cursorPos);
      }
    });
  };

  const handleMentionFilter = (newValue: string, cursorPos: number) => {
    if (!availableFiles || availableFiles.length === 0) {
      setShowFileMention(false);
      return;
    }

    const textBeforeCursor = newValue.slice(0, cursorPos);
    const atMatch = textBeforeCursor.match(/@([^\s]*)$/);

    if (atMatch) {
      const query = atMatch[1].toLowerCase();
      const startPos = textBeforeCursor.length - atMatch[0].length;
      const matches = availableFiles.filter((f) => f.toLowerCase().includes(query));
      setMentionStart(startPos);
      setFilteredFiles(matches);
      setFileSelectedIndex(0);
      setShowFileMention(true);
    } else {
      setShowFileMention(false);
    }
  };

  const handleMentionKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!showFileMention || filteredFiles.length === 0) return false;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setFileSelectedIndex((prev) => (prev + 1) % filteredFiles.length);
      return true;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setFileSelectedIndex(
        (prev) => (prev - 1 + filteredFiles.length) % filteredFiles.length
      );
      return true;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      handleFileMentionSelect(filteredFiles[fileSelectedIndex]);
      return true;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setShowFileMention(false);
      return true;
    }

    return false;
  };

  return {
    showFileMention,
    setShowFileMention,
    filteredFiles,
    fileSelectedIndex,
    setFileSelectedIndex,
    handleFileMentionSelect,
    handleMentionFilter,
    handleMentionKeyDown,
  };
}
