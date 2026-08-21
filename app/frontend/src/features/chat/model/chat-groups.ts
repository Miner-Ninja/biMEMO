import type { Chat } from "@/features/chat/model/chat";

/** Language-independent group keys; display labels come from the dictionary. */
export type ChatGroup = "today" | "yesterday" | "previous7Days" | "older";

/**
 * Group chats by relative date
 */
export function groupChatsByDate(chats: Chat[]): Map<ChatGroup, Chat[]> {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000);
  const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);

  const groups = new Map<ChatGroup, Chat[]>();

  // Sort chats by updatedAt descending
  const sortedChats = [...chats].sort(
    (a, b) => b.updatedAt.getTime() - a.updatedAt.getTime()
  );

  for (const chat of sortedChats) {
    const chatDate = new Date(chat.updatedAt);
    let group: ChatGroup;

    if (chatDate >= today) {
      group = "today";
    } else if (chatDate >= yesterday) {
      group = "yesterday";
    } else if (chatDate >= weekAgo) {
      group = "previous7Days";
    } else {
      group = "older";
    }

    if (!groups.has(group)) {
      groups.set(group, []);
    }
    groups.get(group)!.push(chat);
  }

  return groups;
}

/**
 * Get ordered group keys for display
 */
export const GROUP_ORDER: ChatGroup[] = [
  "today",
  "yesterday",
  "previous7Days",
  "older",
];
