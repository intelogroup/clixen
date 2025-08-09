"use client";

import { useState } from "react";
import { useQuery, useMutation } from "convex/react";
import { api } from "../../../convex/_generated/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Bot, User } from "lucide-react";

export default function SimpleChat() {
  const messages = useQuery(api.messages.list);
  const sendMessage = useMutation(api.messages.send);
  
  const [newMessageText, setNewMessageText] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!newMessageText.trim()) return;
    
    await sendMessage({ 
      body: newMessageText, 
      author: "User" 
    });
    
    setNewMessageText("");
    
    // Simulate AI response after a delay
    setTimeout(async () => {
      await sendMessage({
        body: `I received your message: "${newMessageText}". How can I help you with automation workflows?`,
        author: "AI Assistant"
      });
    }, 1000);
  };

  return (
    <div className="min-h-screen bg-gray-50 p-4">
      <div className="max-w-2xl mx-auto">
        <div className="bg-white rounded-lg shadow-sm border">
          {/* Header */}
          <div className="p-4 border-b">
            <h1 className="text-xl font-semibold flex items-center gap-2">
              <Bot className="h-5 w-5 text-blue-600" />
              Simple Clixen Chat
            </h1>
          </div>
          
          {/* Messages */}
          <div className="h-96 overflow-y-auto p-4 space-y-4">
            {messages?.map((message, index) => (
              <div
                key={index}
                className={`flex items-start gap-3 ${
                  message.author === "User" ? "flex-row-reverse" : ""
                }`}
              >
                <Avatar className="w-8 h-8">
                  <AvatarFallback>
                    {message.author === "User" ? (
                      <User className="h-4 w-4" />
                    ) : (
                      <Bot className="h-4 w-4 text-blue-600" />
                    )}
                  </AvatarFallback>
                </Avatar>
                
                <Card className={`max-w-xs ${
                  message.author === "User" 
                    ? "bg-blue-50 border-blue-200" 
                    : "bg-gray-50"
                }`}>
                  <CardContent className="p-3">
                    <div className="text-sm font-medium text-gray-600 mb-1">
                      {message.author}
                    </div>
                    <div className="text-gray-800">{message.body}</div>
                  </CardContent>
                </Card>
              </div>
            ))}
          </div>
          
          {/* Input */}
          <div className="p-4 border-t">
            <form onSubmit={handleSubmit} className="flex gap-2">
              <Input
                value={newMessageText}
                onChange={(e) => setNewMessageText(e.target.value)}
                placeholder="Type a message..."
                className="flex-1"
              />
              <Button type="submit">Send</Button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
