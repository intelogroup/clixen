export function logIncoming(msg: unknown, text: string): Promise<void>;
export function fetchRecentHistory(jid: string, limit?: number): Promise<Array<{
  push_name: string | null;
  from_me: number;
  role: string;
  speaker_name: string | null;
  text: string;
  ts: number;
}>>;
export function findContactJid(query: string): Promise<string | null>;
export function archiveAvailable(): Promise<boolean>;
export function logOutgoing(jid: string, text: string, msgId?: string | null): Promise<void>;
export function loadContacts(): Promise<Array<{
  jid: string;
  lid?: string | null;
  name?: string | null;
  notify?: string | null;
  verifiedName?: string | null;
  lastSeenAt: number;
}>>;
export function logOwnerJids(jids: string[]): Promise<void>;
export function logContact(contact: {
  jid: string;
  lid?: string | null;
  name?: string | null;
  notify?: string | null;
  verifiedName?: string | null;
  lastSeenAt?: number;
}): Promise<void>;
