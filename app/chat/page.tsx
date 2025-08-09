"use client";

import { useState } from "react";
import { useQuery, useMutation } from "convex/react";
import { api } from "../../convex/_generated/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Bot, User, ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

export default function ChatPage() {
  const { user } = useAuth();
  const router = useRouter();
  const messages = useQuery(api.messages.list);
  const sendMessage = useMutation(api.messages.send);
  
  const [newMessageText, setNewMessageText] = useState("");
  const [isAiThinking, setIsAiThinking] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!newMessageText.trim() || isAiThinking) return;
    
    const messageText = newMessageText;
    
    // Send user message
    await sendMessage({ 
      body: messageText, 
      author: user?.firstName || "User" 
    });
    
    setNewMessageText("");
    setIsAiThinking(true);
    
    // Simulate AI processing time
    setTimeout(async () => {
      const aiResponse = generateAiResponse(messageText);
      await sendMessage({
        body: aiResponse,
        author: "Clixen AI"
      });
      setIsAiThinking(false);
    }, 1000 + Math.random() * 2000);
  };

  const generateAiResponse = (userMessage: string): string => {
    const lowerMessage = userMessage.toLowerCase();
    
    if (lowerMessage.includes("workflow") || lowerMessage.includes("automate")) {
      return "I'd be happy to help you create a workflow! What specific task would you like to automate? For example, I can help with email automation, file syncing, social media posting, or data processing.";
    } else if (lowerMessage.includes("email")) {
      return "Great! For email automation, I can help you set up workflows for sending newsletters, automated responses, email notifications, or email list management. What type of email automation do you need?";
    } else if (lowerMessage.includes("help")) {
      return "I'm here to help you create powerful automation workflows! You can ask me to:\n\n• Create email automation workflows\n• Set up file syncing between services\n• Automate social media posts\n• Build data processing pipelines\n• Connect different apps and services\n\nWhat would you like to automate today?";
    } else {
      return `I understand you're interested in "${userMessage}". I can help you turn this into an automated workflow. Would you like me to suggest some automation ideas based on what you've described?`;
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto h-screen flex flex-col">
        {/* Header */}
        <div className="bg-white border-b p-4">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => router.push("/dashboard")}
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <Bot className="h-6 w-6 text-blue-600" />
            <h1 className="text-xl font-semibold">Clixen AI Chat</h1>
          </div>
        </div>
        
        {/* Messages Area */}
        <div className="flex-1 overflow-y-auto p-6">
          {!messages || messages.length === 0 ? (
            <div className="text-center py-12">
              <Bot className="h-16 w-16 text-blue-600 mx-auto mb-4" />
              <h2 className="text-2xl font-bold text-gray-900 mb-2">
                Welcome to Clixen AI!
              </h2>
              <p className="text-gray-600 max-w-md mx-auto">
                I help you create automation workflows using natural language. 
                Just describe what you'd like to automate and I'll guide you through it.
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((message, index) => (
                <div
                  key={index}
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
                      <div className="text-sm font-medium text-gray-600 mb-2">
                        {message.author}
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
        </div>
      </div>
    </div>
  );
}
