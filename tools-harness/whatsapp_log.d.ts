export function logIncoming(msg: unknown, text: string): Promise<void>;
export function logOutgoing(jid: string, text: string, msgId?: string | null): Promise<void>;
