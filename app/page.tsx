"use client"

import { useState } from "react"
import { ChatHeader } from "@/components/chat/chat-header"
import { ChatMessage } from "@/components/chat/chat-message"
import { ChatInput } from "@/components/chat/chat-input"

export default function Home() {
  const [messages, setMessages] = useState([
    {
      id: 1,
      type: "ai" as const,
      content: "Welcome to Clixen AI! I help you create powerful automation workflows using natural language. Just tell me what you'd like to automate, and I'll guide you through creating it step by step.\n\nTry something like: \"Send me an email every morning with the weather forecast\" or \"Backup my files to cloud storage weekly\"",
      timestamp: "Now"
    }
  ])
  const [isCreatingWorkflow, setIsCreatingWorkflow] = useState(false)

  const handleSendMessage = (message: string) => {
    // Add user message
    const userMessage = {
      id: Date.now(),
      type: "user" as const,
      content: message,
      timestamp: "Now"
    }
    setMessages(prev => [...prev, userMessage])

    // Simulate AI response
    setTimeout(() => {
      setIsCreatingWorkflow(true)
      const aiMessage = {
        id: Date.now() + 1,
        type: "ai" as const,
        content: "Perfect! I can create that workflow for you. Here's what I understand:",
        timestamp: "Now",
        workflowSummary: {
          name: "Daily Email Automation",
          trigger: "Daily at 8:00 AM",
          action: "Fetch weather data",
          output: "Send formatted email"
        },
        progress: [
          { step: "Weather API configured", status: "completed" as const },
          { step: "Email template created", status: "completed" as const },
          { step: "Daily schedule set", status: "completed" as const },
          { step: "Deploying to n8n...", status: "in-progress" as const }
        ]
      }
      setMessages(prev => [...prev, aiMessage])

      // Complete workflow creation after 3 seconds
      setTimeout(() => {
        setIsCreatingWorkflow(false)
      }, 3000)
    }, 1000)
  }

  return (
    <div className="min-h-screen bg-background">
      <ChatHeader />

      <div className="max-w-4xl mx-auto p-4">
        <div className="bg-card rounded-lg border h-[calc(100vh-120px)] flex flex-col">
          {/* Chat Messages */}
          <div className="flex-1 p-6 overflow-y-auto space-y-6">
            {messages.map((message) => (
              <ChatMessage
                key={message.id}
                type={message.type}
                content={message.content}
                timestamp={message.timestamp}
                workflowSummary={message.workflowSummary}
                progress={message.progress}
              />
            ))}
          </div>

          {/* Input Area */}
          <ChatInput
            onSendMessage={handleSendMessage}
            disabled={isCreatingWorkflow}
          />
        </div>
      </div>
    </div>
  )
}
