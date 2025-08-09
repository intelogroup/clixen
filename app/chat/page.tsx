"use client"

import { useState, useRef, useEffect } from "react"
import { useRouter } from "next/navigation"
import { ChatSidebar } from "@/components/chat/chat-sidebar"
import { ChatMain } from "@/components/chat/chat-main"
import { FloatingActions } from "@/components/chat/floating-actions"
import { TransitionManager, TransitionType } from "@/components/transitions/transition-manager"
import { ConversationEngine } from "@/lib/conversation-engine"

export default function ChatPage() {
  const router = useRouter()
  const conversationEngine = useRef(new ConversationEngine())
  const [currentChatId, setCurrentChatId] = useState<string>()
  const [messages, setMessages] = useState<Array<{
    id: number
    type: "ai" | "user"
    content: string
    timestamp: string
    workflowSummary?: any
    progress?: any[]
    questions?: string[]
    options?: Array<{ label: string; value: string }>
    actionButtons?: Array<{
      label: string
      action: string
      variant?: "default" | "outline" | "destructive" | "secondary"
      icon?: React.ReactNode
    }>
    errorMessage?: string
    samplePreview?: {
      title: string
      content: string
    }
    showSuggestions?: boolean
  }>>([])
  const [isCreatingWorkflow, setIsCreatingWorkflow] = useState(false)
  const [currentTransition, setCurrentTransition] = useState<TransitionType>(null)
  const [pendingWorkflowType, setPendingWorkflowType] = useState<string>()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [isTyping, setIsTyping] = useState(false)

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    const chatBottom = document.getElementById('chat-bottom')
    if (chatBottom) {
      chatBottom.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])

  const handleSendMessage = async (message: string) => {
    // Add user message
    const userMessage = {
      id: Date.now(),
      type: "user" as const,
      content: message,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }
    setMessages(prev => [...prev, userMessage])

    // Show AI typing indicator
    setIsTyping(true)

    try {
      // Process message through conversation engine
      const response = await conversationEngine.current.processMessage(message)

      // Simulate realistic AI response delay
      await new Promise(resolve => setTimeout(resolve, 1000 + Math.random() * 2000))

      setIsTyping(false)

      // Check if we need to show workflow creation transition
      if (response.nextState.phase === "creating") {
        setIsCreatingWorkflow(true)
        setPendingWorkflowType(response.nextState.workflowType || "workflow")
        setCurrentTransition("workflow-generation")
      }

      // Add AI response
      const aiMessage = {
        id: Date.now() + 1,
        type: "ai" as const,
        content: response.content,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        workflowSummary: response.workflowSummary,
        progress: response.progress,
        questions: response.questions,
        options: response.options,
        actionButtons: response.actionButtons,
        errorMessage: response.errorMessage,
        samplePreview: response.samplePreview,
        showSuggestions: response.nextState.phase === "welcome" && !response.workflowSummary
      }

      setMessages(prev => [...prev, aiMessage])

    } catch (error) {
      setIsTyping(false)
      console.error('Error processing message:', error)

      // Add error message
      const errorMessage = {
        id: Date.now() + 1,
        type: "ai" as const,
        content: "I apologize, but I encountered an error processing your request. Please try again or rephrase your message.",
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        errorMessage: "Failed to process message"
      }

      setMessages(prev => [...prev, errorMessage])
    }
  }

  const handleNewChat = () => {
    setCurrentChatId(undefined)
    setMessages([])
    conversationEngine.current.reset()
    setIsCreatingWorkflow(false)
    setCurrentTransition(null)
    setPendingWorkflowType(undefined)
  }

  const handleSelectChat = (chatId: string) => {
    setCurrentChatId(chatId)
    // In a real app, this would load chat history from a database
    // For demo purposes, we'll load sample conversations based on chat ID
    loadSampleConversation(chatId)
  }

  const loadSampleConversation = (chatId: string) => {
    const sampleConversations = {
      "1": [
        {
          id: 1,
          type: "user" as const,
          content: "Create a daily weather report workflow",
          timestamp: "Yesterday 2:30 PM"
        },
        {
          id: 2,
          type: "ai" as const,
          content: "Perfect! I've created your daily weather email workflow.",
          timestamp: "Yesterday 2:32 PM",
          workflowSummary: {
            name: "Daily Weather Reports",
            trigger: "Every day at 8:00 AM",
            action: "Fetch weather data and send email",
            output: "Daily weather email",
            status: "active" as const,
            workflowId: "wf_weather_123",
            nextRun: "Tomorrow at 8:00 AM"
          }
        }
      ],
      "2": [
        {
          id: 1,
          type: "user" as const,
          content: "I need to backup my Dropbox files to Google Drive",
          timestamp: "2 days ago"
        },
        {
          id: 2,
          type: "ai" as const,
          content: "I'll create your backup automation workflow.",
          timestamp: "2 days ago",
          workflowSummary: {
            name: "Dropbox to Google Drive Backup",
            trigger: "Weekly on Sundays",
            action: "Sync files between cloud storage",
            output: "Automated backups",
            status: "active" as const
          }
        }
      ]
    }

    const conversation = sampleConversations[chatId as keyof typeof sampleConversations] || []
    setMessages(conversation)
  }

  const handleMessageAction = async (messageId: number, action: string, value?: string) => {
    console.log('Message action:', action, value)

    // Handle message-specific actions
    switch (action) {
      case "option":
        if (value) {
          await handleSendMessage(value)
        }
        break
      case "proceed":
        await handleSendMessage("Yes, proceed with creating the workflow")
        break
      case "modify":
        await handleSendMessage("I'd like to modify the settings")
        break
      case "view_dashboard":
        router.push("/dashboard")
        break
      case "auto_retry":
        await handleSendMessage("Please retry automatically")
        break
      case "reauth":
        await handleSendMessage("I'll re-authenticate my accounts")
        break
      default:
        console.log('Unhandled action:', action)
    }
  }

  const handleFloatingAction = (action: string) => {
    switch (action) {
      case "new-workflow":
        handleNewChat()
        break
      case "templates":
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

  const handleTransitionComplete = (workflowId?: string) => {
    setCurrentTransition(null)
    setIsCreatingWorkflow(false)

    // Add AI response after workflow generation
    const aiMessage = {
      id: Date.now() + 1,
      type: "ai" as const,
      content: `Perfect! I've created your ${pendingWorkflowType} for you. The workflow is now ready and configured with all the necessary integrations.`,
      timestamp: "Now",
      workflowSummary: {
        name: pendingWorkflowType || "New Workflow",
        trigger: "Configurable trigger",
        action: "Smart automation",
        output: "Targeted results"
      },
      workflowId
    }
    setMessages(prev => [...prev, aiMessage])
    setPendingWorkflowType(undefined)
  }

  return (
    <TransitionManager
      type={currentTransition}
      workflowType={pendingWorkflowType}
      onComplete={handleTransitionComplete}
    >
      <div className="min-h-screen bg-background flex relative">
        {/* Desktop Sidebar */}
        <div className={`hidden md:block transition-all duration-300 ${sidebarCollapsed ? 'w-16' : 'w-[280px]'}`}>
          <ChatSidebar
            currentChatId={currentChatId}
            onNewChat={handleNewChat}
            onSelectChat={handleSelectChat}
            onNavigate={(route) => router.push(route)}
            className={sidebarCollapsed ? 'w-16' : 'w-[280px]'}
          />
        </div>

        {/* Mobile Sidebar Overlay */}
        <div className={`md:hidden fixed inset-0 z-50 ${sidebarCollapsed ? 'pointer-events-none' : ''}`}>
          <div
            className={`absolute inset-0 bg-black/20 transition-opacity ${
              sidebarCollapsed ? 'opacity-0' : 'opacity-100'
            }`}
            onClick={() => setSidebarCollapsed(true)}
          />
          <div className={`absolute left-0 top-0 h-full transition-transform ${
            sidebarCollapsed ? '-translate-x-full' : 'translate-x-0'
          }`}>
            <ChatSidebar
              currentChatId={currentChatId}
              onNewChat={handleNewChat}
              onSelectChat={handleSelectChat}
              onNavigate={(route) => router.push(route)}
            />
          </div>
        </div>

        <ChatMain
          messages={messages}
          onSendMessage={handleSendMessage}
          onMessageAction={handleMessageAction}
          isCreatingWorkflow={isCreatingWorkflow || isTyping}
          chatTitle={currentChatId ? `Chat ${currentChatId}` : "New Workflow Chat"}
          userName="Kalinov jim rozensky Dameus"
        />

        <FloatingActions onAction={handleFloatingAction} />
      </div>
    </TransitionManager>
  )
}
