"use client"

import { useState } from "react"
import { ChatSidebar } from "@/components/chat/chat-sidebar"
import { ChatMain } from "@/components/chat/chat-main"

export default function ChatPage() {
  const [currentChatId, setCurrentChatId] = useState<string>()
  const [messages, setMessages] = useState([
    {
      id: 1,
      type: "user" as const,
      content: "Create a workflow that sends me daily weather updates for my city",
      timestamp: "Now"
    },
    {
      id: 2,
      type: "ai" as const,
      content: "I'll create a weather notification workflow for you! Here's what I'll set up:\n\n✅ Weather API Integration\n📱 Daily SMS/Email Notifications\n🕐 Customizable Schedule\n\nWould you like me to proceed with the setup?",
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
          name: "Daily Weather Updates",
          trigger: "Daily at 8:00 AM",
          action: "Fetch weather data",
          output: "Send formatted notification"
        },
        progress: [
          { step: "Weather API Integration", status: "completed" as const },
          { step: "Daily SMS/Email Notifications", status: "completed" as const },
          { step: "Customizable Schedule", status: "in-progress" as const }
        ]
      }
      setMessages(prev => [...prev, aiMessage])

      // Complete workflow creation after 3 seconds
      setTimeout(() => {
        setIsCreatingWorkflow(false)
      }, 3000)
    }, 1000)
  }

  const handleNewChat = () => {
    setCurrentChatId(undefined)
    setMessages([
      {
        id: 1,
        type: "ai" as const,
        content: "Welcome to Clixen AI! I help you create powerful automation workflows using natural language. Just tell me what you'd like to automate, and I'll guide you through creating it step by step.\n\nTry something like: \"Send me an email every morning with the weather forecast\" or \"Backup my files to cloud storage weekly\"",
        timestamp: "Now"
      }
    ])
  }

  const handleSelectChat = (chatId: string) => {
    setCurrentChatId(chatId)
    // Load chat messages for selected chat
    // For now, just show the current conversation
  }

  return (
    <div className="min-h-screen bg-background flex">
      <ChatSidebar
        currentChatId={currentChatId}
        onNewChat={handleNewChat}
        onSelectChat={handleSelectChat}
      />

      <ChatMain
        messages={messages}
        onSendMessage={handleSendMessage}
        isCreatingWorkflow={isCreatingWorkflow}
        chatTitle="New Workflow Chat"
      />
    </div>
  )
}
