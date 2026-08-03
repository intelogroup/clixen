const CLIXEN_API_URL = process.env.CLIXEN_API_URL ?? "http://127.0.0.1:9234";
const SESSION_COOKIE = "g4l_session";

export function backendUrl(path: string): string {
  return `${CLIXEN_API_URL}${path}`;
}

export function sessionCookieName(): string {
  return SESSION_COOKIE;
}
