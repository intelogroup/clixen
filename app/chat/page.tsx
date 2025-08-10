"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Bot, User, History } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCurrentUser, useAuthActions } from "@/lib/auth-context";
import { DashboardSidebar } from "@/components/dashboard/dashboard-sidebar";
import { MobileSidebar } from "@/components/ui/mobile-sidebar";

interface Message {
  id: string;
  author: string;
  body: string;
  timestamp: Date;
}

export default function ChatPage() {
  const user = useCurrentUser();
  const { signOut } = useAuthActions();
  const router = useRouter();
  const [messages, setMessages] = useState<Message[]>([]);
  const [newMessageText, setNewMessageText] = useState("");
  const [isAiThinking, setIsAiThinking] = useState(false);
  const [chatHistory, setChatHistory] = useState<Array<{id: string, title: string, timestamp: string}>>([]);

  // Load messages and chat history from localStorage on mount
  useEffect(() => {
    const saved = localStorage.getItem('chat-messages');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        setMessages(parsed.map((m: any) => ({
          ...m,
          timestamp: new Date(m.timestamp)
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

  // Save messages to localStorage whenever they change
  useEffect(() => {
    if (messages.length > 0) {
      localStorage.setItem('chat-messages', JSON.stringify(messages));
    }
  }, [messages]);

  const addMessage = (author: string, body: string) => {
    const newMessage: Message = {
      id: Date.now().toString(),
      author,
      body,
      timestamp: new Date()
    };
    setMessages(prev => [...prev, newMessage]);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!newMessageText.trim() || isAiThinking) return;
    
    const messageText = newMessageText;
    setNewMessageText("");
    
    // Add user message
    addMessage(user?.firstName || "User", messageText);
    
    setIsAiThinking(true);
    
    // Simulate AI processing time
    setTimeout(() => {
      const aiResponse = generateAiResponse(messageText);
      addMessage("Clixen AI", aiResponse);
      setIsAiThinking(false);
    }, 1000 + Math.random() * 2000);
  };

  const handleNewChat = () => {
    if (messages.length > 0) {
      // Save current chat to history
      const chatTitle = messages[0]?.body.slice(0, 30) + "..." || "New Chat";
      const newChat = {
        id: Date.now().toString(),
        title: chatTitle,
        timestamp: new Date().toLocaleDateString()
      };
      const updatedHistory = [newChat, ...chatHistory.slice(0, 4)]; // Keep only 5 recent chats
      setChatHistory(updatedHistory);
      localStorage.setItem('chat-history', JSON.stringify(updatedHistory));
    }
    
    setMessages([]);
    localStorage.removeItem('chat-messages');
  };

  const handleSignOut = async () => {
    await signOut();
    router.push('/auth/signin');
  };

  const generateAiResponse = (userMessage: string): string => {
    const lowerMessage = userMessage.toLowerCase();
    
    if (lowerMessage.includes("daily weather") || lowerMessage.includes("weather email") || lowerMessage.includes("weather updates")) {
      return `🎯 Perfect! I'll create a weather notification workflow for you. Here's what I'll set up:

📋 **Workflow Overview:**
• Weather API integration
• Daily email notifications  
• Customizable schedule

Quick questions to finalize:
1. What's your email address?
2. What timezone should I use?
3. Which city's weather would you like?
4. Any specific weather details you want included?

Would you like me to proceed with this setup?`;
    } else if (lowerMessage.includes("customer onboarding") || lowerMessage.includes("new signup")) {
      return `🚀 Excellent! Customer onboarding automation is one of the most valuable workflows you can create.

📈 **Customer Onboarding Workflow:**
• **Trigger:** New user signup
• **Immediate Actions:** Welcome email, CRM sync, team notification
• **Follow-up:** Check-in emails, feedback surveys

To set this up perfectly, I need some details:
1. How do I detect new signups? (Webhook URL?)
2. Which CRM system? (HubSpot, Salesforce, etc.)
3. Team notification method? (Slack, email?)
4. From email address for customer communications?

Ready to build this automation?`;
    } else if (lowerMessage.includes("email") && (lowerMessage.includes("daily") || lowerMessage.includes("report"))) {
      return `📧 Great choice! Daily email reports keep teams aligned and informed.

📋 **Daily Report Workflow:**
• **Schedule:** Configurable time (e.g., 9:00 AM EST)
• **Data Sources:** Analytics, sales metrics, user activity
• **Recipients:** Team members, stakeholders
• **Format:** Professional summary with key metrics

Questions to get started:
1. What data should be included in the reports?
2. Who should receive these emails?
3. What time should they be sent?
4. How often? (Daily, weekly, etc.)

Shall I create this workflow for you?`;
    } else if (lowerMessage.includes("backup") || lowerMessage.includes("sync files")) {
      return `💾 Smart thinking! File backup automation ensures your data is always safe.

🔄 **File Backup Workflow:**
• **Source:** Your files/folders
• **Destination:** Cloud storage (Google Drive, Dropbox, etc.)
• **Schedule:** Regular intervals (daily, weekly)
• **Features:** Versioning, compression, notifications

Setup questions:
1. Which files/folders need backing up?
2. Preferred cloud storage service?
3. How often should backups run?
4. Should you get confirmation emails?

Ready to set up your backup automation?`;
    } else if (lowerMessage.includes("help") || lowerMessage.includes("what can you")) {
      return `👋 I'm here to help you create powerful automation workflows! Here's what I can do:

**Popular Automations:**
• 📧 Email workflows (newsletters, responses, reports)
• 📊 Data processing and reporting
• 🔄 File backup and synchronization
• 📱 Social media management
• 🎯 Customer communication flows
• ⚡ Business process automation

**How it works:**
1. Tell me what you want to automate
2. I'll ask clarifying questions
3. I'll create the workflow for you
4. Deploy and start automating!

What would you like to automate today?`;
    } else if (lowerMessage.includes("clear") || lowerMessage.includes("reset")) {
      setTimeout(() => {
        setMessages([]);
        localStorage.removeItem('chat-messages');
      }, 500);
      return "✅ Chat cleared! How can I help you create a new workflow?";
    } else if (lowerMessage.includes("social media") || lowerMessage.includes("social post")) {
      return `📱 Social media automation can save hours every week!

🎯 **Social Media Workflow:**
• **Content:** Auto-posting, scheduling, cross-platform sharing
• **Sources:** RSS feeds, content calendars, user-generated content
• **Platforms:** Twitter, LinkedIn, Facebook, Instagram
• **Features:** Hashtag optimization, timing optimization

Setup questions:
1. Which social media platforms?
2. What type of content? (blog posts, images, updates)
3. How often should posts go out?
4. Any specific hashtags or mentions to include?

Let's automate your social presence!`;
    } else {
      return `I understand you want to work with "${userMessage}". I can help turn this into a powerful automation workflow!

🤔 **Let me suggest some possibilities:**
• If this involves regular tasks → I can schedule them
• If this needs notifications → I can set up alerts
• If this requires data processing → I can automate it
• If this involves multiple steps → I can create a complete flow

Could you tell me more about what specific automation you have in mind? The more details you provide, the better I can help you build the perfect workflow!`;
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-purple-50 flex">
      {/* Mobile Sidebar */}
      <MobileSidebar onSignOut={handleSignOut} />

      {/* Desktop Sidebar - Hidden on mobile */}
      <div className="hidden md:block relative">
        <DashboardSidebar onSignOut={handleSignOut} />
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex h-screen">
        {/* Chat Interface */}
        <div className="flex-1 flex flex-col max-w-4xl mx-auto w-full">
          {/* Header */}
          <div className="bg-white/80 backdrop-blur-sm border-b border-white/20 p-4 shadow-sm">
            <div className="flex items-center gap-3">
              <Bot className="h-6 w-6 text-blue-600" />
              <h1 className="text-xl font-semibold bg-gradient-to-r from-slate-900 to-slate-700 bg-clip-text text-transparent">
                Clixen AI Chat
              </h1>
              <div className="ml-auto text-sm text-gray-500">
                {messages.length} messages
              </div>
            </div>
          </div>

          {/* Chat History Header */}
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
                  I help you create automation workflows using natural language. 
                  Just describe what you'd like to automate and I'll guide you through it.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-lg mx-auto text-sm">
                  <Button 
                    variant="outline" 
                    onClick={() => setNewMessageText("Help me create an email automation workflow")}
                  >
                    Email Automation
                  </Button>
                  <Button 
                    variant="outline" 
                    onClick={() => setNewMessageText("I want to automate file backups")}
                  >
                    File Backup
                  </Button>
                  <Button 
                    variant="outline" 
                    onClick={() => setNewMessageText("Set up social media posting")}
                  >
                    Social Media
                  </Button>
                  <Button 
                    variant="outline" 
                    onClick={() => setNewMessageText("Create a data processing pipeline")}
                  >
                    Data Processing
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
                        <div className="text-gray-800 whitespace-pre-wrap">
                          {message.body}
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                ))}
                
                {/* AI Thinking Indicator */}
                {isAiThinking && (
                  <div className="flex items-start gap-3">
                    <Avatar className="w-8 h-8">
                      <AvatarFallback>
                        <Bot className="h-4 w-4 text-blue-600" />
                      </AvatarFallback>
                    </Avatar>
                    <Card className="bg-white border-gray-200">
                      <CardContent className="p-4">
                        <div className="flex items-center gap-2">
                          <div className="flex space-x-1">
                            <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce"></div>
                            <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style={{animationDelay: '0.1s'}}></div>
                            <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style={{animationDelay: '0.2s'}}></div>
                          </div>
                          <span className="text-sm text-gray-600">AI is thinking...</span>
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                )}
              </div>
            )}
          </div>
          
          {/* Input Area */}
          <div className="bg-white border-t p-4">
            <form onSubmit={handleSubmit} className="flex gap-3">
              <Input
                value={newMessageText}
                onChange={(e) => setNewMessageText(e.target.value)}
                placeholder={isAiThinking ? "AI is thinking..." : "Describe the workflow you'd like to create..."}
                className="flex-1"
                disabled={isAiThinking}
              />
              <Button type="submit" disabled={isAiThinking || !newMessageText.trim()}>
                Send
              </Button>
            </form>
            <div className="flex gap-2 mt-2">
              <Button 
                variant="ghost" 
                size="sm"
                onClick={() => setNewMessageText("Clear chat history")}
              >
                Clear History
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
  );
}
