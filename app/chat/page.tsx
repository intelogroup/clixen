"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Bot, User, History, AlertTriangle, Square } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCurrentUser, useAuthActions } from "@/lib/auth-context";
import { RequireAuth } from "@/components/auth/require-auth";
import { DashboardSidebar } from "@/components/dashboard/dashboard-sidebar";
import { MobileSidebar } from "@/components/ui/mobile-sidebar";

interface Message {
  id: string;
  author: string;
  body: string;
  timestamp: Date;
  streaming?: boolean;
}

export default function ChatPage() {
  const user = useCurrentUser();
  const { signOut } = useAuthActions();
  const router = useRouter();
  const [messages, setMessages] = useState<Message[]>([]);
  const [newMessageText, setNewMessageText] = useState("");
  const [isAiThinking, setIsAiThinking] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [chatHistory, setChatHistory] = useState<Array<{id: string, title: string, timestamp: string}>>([]);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const messagesRef = useRef<Message[]>([]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const userName =
    user?.displayName ||
    ([user?.firstName, user?.lastName].filter(Boolean).join(" ").trim() as string) ||
    "User";

  // Load messages and chat history from localStorage on mount
  useEffect(() => {
    const saved = localStorage.getItem('chat-messages');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        setMessages(parsed.map((m: any) => ({
          ...m,
          timestamp: new Date(m.timestamp),
          streaming: false
        })));
      } catch (e) {
        console.error('Failed to parse saved messages:', e);
      }
    }

    const savedHistory = localStorage.getItem('chat-history');
    if (savedHistory) {
      try {
        setChatHistory(JSON.parse(savedHistory));
      } catch (e) {
        console.error('Failed to parse chat history:', e);
      }
    }
  }, []);

  // Save messages to localStorage whenever they change (skip streaming drafts)
  useEffect(() => {
    const settled = messages.map(({ streaming, ...m }) => m);
    if (settled.length > 0) {
      localStorage.setItem('chat-messages', JSON.stringify(settled));
    }
  }, [messages]);

  const appendAssistantToken = (id: string, token: string) => {
    setMessages(prev => prev.map(m =>
      m.id === id ? { ...m, body: m.body + token, streaming: true } : m
    ));
  };

  const finalizeAssistant = (id: string) => {
    setMessages(prev => prev.map(m =>
      m.id === id ? { ...m, streaming: false } : m
    ));
  };

  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isAiThinking) return;

    setStreamError(null);
    setNewMessageText("");

    const userMsg: Message = {
      id: `u-${Date.now()}`,
      author: userName,
      body: trimmed,
      timestamp: new Date(),
    };
    const aiId = `ai-${Date.now()}`;
    const aiMsg: Message = {
      id: aiId,
      author: "Clixen AI",
      body: "",
      timestamp: new Date(),
      streaming: true,
    };

    setMessages(prev => [...prev, userMsg, aiMsg]);
    setIsAiThinking(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(
        `/api/chat/stream?message=${encodeURIComponent(trimmed)}&chat_id=${encodeURIComponent('web_ui')}`,
        { signal: controller.signal }
      );

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Backend returned ${res.status}`);
      }

      if (!res.body) throw new Error("Empty response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let firstTokenSeen = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";

        for (const chunk of lines) {
          if (!chunk.startsWith("data:")) continue;
          const payload = chunk.slice(5).trim();
          let event: any;
          try {
            event = JSON.parse(payload);
          } catch {
            continue;
          }

          if (event.type === "ping") continue;
          if (event.token) {
            if (!firstTokenSeen) {
              firstTokenSeen = true;
              setIsAiThinking(false);
            }
            appendAssistantToken(aiId, event.token);
          }
          if (event.error) {
            setStreamError(typeof event.error === "string" ? event.error : "Stream failed");
            finalizeAssistant(aiId);
            setIsAiThinking(false);
            return;
          }
          if (event.done) {
            finalizeAssistant(aiId);
            setIsAiThinking(false);
          }
        }
      }
    } catch (err: any) {
      if (err.name === "AbortError") {
        finalizeAssistant(aiId);
        setIsAiThinking(false);
        return;
      }
      setStreamError(err?.message || "Failed to reach the Clixen backend. Is it running?");
      finalizeAssistant(aiId);
      setIsAiThinking(false);
    } finally {
      abortRef.current = null;
    }
  }, [isAiThinking, userName]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(newMessageText);
  };

  const handleStop = () => {
    abortRef.current?.abort();
  };

  const handleNewChat = () => {
    abortRef.current?.abort();
    if (messages.length > 0) {
      const firstUser = messages.find(m => m.author !== "Clixen AI");
      const chatTitle = (firstUser?.body || "New Chat").slice(0, 30) + (firstUser ? "..." : "");
      const newChat = {
        id: Date.now().toString(),
        title: chatTitle,
        timestamp: new Date().toLocaleDateString()
      };
      const updatedHistory = [newChat, ...chatHistory.slice(0, 4)];
      setChatHistory(updatedHistory);
      localStorage.setItem('chat-history', JSON.stringify(updatedHistory));
    }
    setMessages([]);
    setIsAiThinking(false);
    setStreamError(null);
    localStorage.removeItem('chat-messages');
  };

  const handleSignOut = async () => {
    abortRef.current?.abort();
    await signOut();
    router.push('/auth/signin');
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <RequireAuth>
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-purple-50 flex">
        <MobileSidebar onSignOut={handleSignOut} />

        <div className="hidden md:block relative">
          <DashboardSidebar onSignOut={handleSignOut} workflows={[]} />
        </div>

        <div className="flex-1 flex h-screen">
          <div className="flex-1 flex flex-col max-w-4xl mx-auto w-full">
            {/* Header */}
            <div className="bg-white/80 backdrop-blur-sm border-b border-white/20 p-4 shadow-sm">
              <div className="flex items-center gap-3">
                <Bot className="h-6 w-6 text-blue-600" />
                <h1 className="text-xl font-semibold bg-gradient-to-r from-slate-900 to-slate-700 bg-clip-text text-transparent">
                  Clixen AI Chat
                </h1>
              </div>
            </div>

            {/* Error banner */}
            {streamError && (
              <div className="px-4 py-2 border-b border-red-200 bg-red-50/80">
                <div className="flex items-center gap-2 text-sm text-red-700">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span className="flex-1">{streamError}</span>
                  <button
                    onClick={() => setStreamError(null)}
                    className="text-red-500 hover:text-red-700 text-xs font-medium"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            )}

            {/* Recent Conversations */}
            {chatHistory.length > 0 && messages.length === 0 && (
              <div className="p-4 border-b border-white/20">
                <div className="flex items-center gap-2 mb-3">
                  <History className="h-4 w-4 text-slate-500" />
                  <span className="text-sm font-medium text-slate-600">Recent Conversations</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {chatHistory.slice(0, 6).map((chat) => (
                    <button
                      key={chat.id}
                      onClick={handleNewChat}
                      className="text-left p-3 rounded-lg bg-white/60 hover:bg-white/80 border border-white/40 transition-all duration-200"
                    >
                      <div className="text-sm font-medium text-slate-700 truncate">{chat.title}</div>
                      <div className="text-xs text-slate-500">{chat.timestamp}</div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Messages Area */}
            <div className="flex-1 overflow-y-auto p-6">
              {messages.length === 0 ? (
                <div className="text-center py-12">
                  <Bot className="h-16 w-16 text-blue-600 mx-auto mb-4" />
                  <h2 className="text-2xl font-bold text-gray-900 mb-2">
                    Welcome to Clixen AI!
                  </h2>
                  <p className="text-gray-600 max-w-md mx-auto mb-6">
                    Ask me anything — I have access to your local files, tools, and
                    automations. Try asking for a real task.
                  </p>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-lg mx-auto text-sm">
                    <Button variant="outline" onClick={() => setNewMessageText("List my recent notes")}>
                      Browse my notes
                    </Button>
                    <Button variant="outline" onClick={() => setNewMessageText("Check today's weather")}>
                      Weather report
                    </Button>
                    <Button variant="outline" onClick={() => setNewMessageText("Summarize my inbox")}>
                      Summarize email
                    </Button>
                    <Button variant="outline" onClick={() => setNewMessageText("What can you help me with?")}>
                      Help
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="space-y-6">
                  {messages.map((message) => (
                    <div
                      key={message.id}
                      className={`flex items-start gap-3 ${
                        message.author !== "Clixen AI" ? "flex-row-reverse" : ""
                      }`}
                    >
                      <Avatar className="w-8 h-8">
                        <AvatarFallback>
                          {message.author === "Clixen AI" ? (
                            <Bot className="h-4 w-4 text-blue-600" />
                          ) : (
                            <User className="h-4 w-4" />
                          )}
                        </AvatarFallback>
                      </Avatar>

                      <Card className={`max-w-md ${
                        message.author !== "Clixen AI"
                          ? "bg-blue-50 border-blue-200"
                          : "bg-white border-gray-200"
                      }`}>
                        <CardContent className="p-4">
                          <div className="flex items-center justify-between mb-2">
                            <div className="text-sm font-medium text-gray-600">
                              {message.author}
                            </div>
                            <div className="text-xs text-gray-400">
                              {message.timestamp.toLocaleTimeString([], {
                                hour: '2-digit',
                                minute: '2-digit'
                              })}
                            </div>
                          </div>
                          {message.body ? (
                            <div className="text-gray-800 whitespace-pre-wrap text-[15px] leading-relaxed">
                              {message.body}
                            </div>
                          ) : (
                            <div className="flex items-center gap-2 py-1">
                              <div className="flex space-x-1">
                                <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce"></div>
                                <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style={{animationDelay: '0.1s'}}></div>
                                <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style={{animationDelay: '0.2s'}}></div>
                              </div>
                              <span className="text-sm text-gray-600">Thinking...</span>
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    </div>
                  ))}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>

            {/* Input Area */}
            <div className="bg-white border-t p-4">
              <form onSubmit={handleSubmit} className="flex gap-3">
                <Input
                  value={newMessageText}
                  onChange={(e) => setNewMessageText(e.target.value)}
                  placeholder={isAiThinking ? "AI is working..." : "Ask Clixen to do something..."}
                  className="flex-1"
                  disabled={isAiThinking}
                />
                {isAiThinking ? (
                  <Button type="button" variant="destructive" onClick={handleStop}>
                    <Square className="h-4 w-4" />
                  </Button>
                ) : (
                  <Button type="submit" disabled={!newMessageText.trim()}>
                    Send
                  </Button>
                )}
              </form>
              <div className="flex gap-2 mt-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleNewChat}
                >
                  New Chat
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setNewMessageText("What can you help me with?")}
                >
                  Help
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </RequireAuth>
  );
}
