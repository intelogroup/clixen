"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { ChatSidebar } from "@/components/chat/chat-sidebar"
import { ChatMain } from "@/components/chat/chat-main"
import { FloatingActions } from "@/components/chat/floating-actions"
import { TransitionManager, TransitionType } from "@/components/transitions/transition-manager"

export default function ChatPage() {
  const router = useRouter()
  const [currentChatId, setCurrentChatId] = useState<string>()
  const [messages, setMessages] = useState([
    {
      id: 1,
      type: "ai" as const,
      content: "Hi John! What workflow would you like to create today?",
      timestamp: "Just now"
    },
    {
      id: 2,
      type: "user" as const,
      content: "I want to automate my social media posting",
      timestamp: "Just now"
    },
    {
      id: 3,
      type: "ai" as const,
      content: "Excellent! Let me help you create a social media automation. Which platforms would you like to post to?",
      timestamp: "Just now",
      showSuggestions: true
    }
  ])
  const [isCreatingWorkflow, setIsCreatingWorkflow] = useState(false)
  const [currentTransition, setCurrentTransition] = useState<TransitionType>(null)
  const [pendingWorkflowType, setPendingWorkflowType] = useState<string>()

  const handleSendMessage = (message: string) => {
    // Add user message
    const userMessage = {
      id: Date.now(),
      type: "user" as const,
      content: message,
      timestamp: "Now"
    }
    setMessages(prev => [...prev, userMessage])

    // Determine workflow type from message
    let workflowType = "automation workflow"
    if (message.toLowerCase().includes("social")) workflowType = "social media automation"
    else if (message.toLowerCase().includes("email")) workflowType = "email automation"
    else if (message.toLowerCase().includes("weather")) workflowType = "weather monitoring"
    else if (message.toLowerCase().includes("backup")) workflowType = "data backup automation"

    // Show workflow generation transition
    setPendingWorkflowType(workflowType)
    setCurrentTransition("workflow-generation")
    setIsCreatingWorkflow(true)
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

  const handleFloatingAction = (action: string) => {
    switch (action) {
      case "new-workflow":
        handleNewChat()
        break
      case "templates":
        // Show templates in input
        handleSendMessage("Show me workflow templates")
        break
      case "integrations":
        handleSendMessage("What integrations are available?")
        break
      case "ai-suggestions":
        handleSendMessage("Give me AI suggestions for workflow automation")
        break
      case "dashboard":
        router.push("/dashboard")
        break
    }
  }

  return (
    <div className="min-h-screen bg-background flex relative">
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

      <FloatingActions onAction={handleFloatingAction} />
    </div>
  )
}
