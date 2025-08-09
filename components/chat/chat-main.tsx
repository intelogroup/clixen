"use client"

import { Settings, User, Bot, ArrowLeft, MoreHorizontal, Menu } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { ChatMessage } from "./chat-message"
import { ChatInput } from "./chat-input"
import { QuickSuggestions } from "./quick-suggestions"
import { TypingIndicator } from "./typing-indicator"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"

interface WorkflowSummary {
  name: string
  trigger: string
  action: string
  output: string
  status?: "active" | "pending" | "error"
  workflowId?: string
  nextRun?: string
}

interface WorkflowProgress {
  step: string
  status: "completed" | "in-progress" | "pending" | "error"
  progress?: number
}

interface QuestionOption {
  label: string
  value: string
}

interface ActionButton {
  label: string
  action: string
  variant?: "default" | "outline" | "destructive" | "secondary"
  icon?: React.ReactNode
}

interface ChatMessage {
  id: number
  type: "ai" | "user"
  content: string
  timestamp: string
  workflowSummary?: WorkflowSummary
  progress?: WorkflowProgress[]
  questions?: string[]
  options?: QuestionOption[]
  actionButtons?: ActionButton[]
  errorMessage?: string
  samplePreview?: {
    title: string
    content: string
  }
  showSuggestions?: boolean
}

interface ChatMainProps {
  chatTitle?: string
  messages: ChatMessage[]
  onSendMessage: (message: string) => void
  onMessageAction?: (messageId: number, action: string, value?: string) => void
  isCreatingWorkflow?: boolean
  userName?: string
  onToggleSidebar?: () => void
  isTyping?: boolean
}

export function ChatMain({
  chatTitle = "New Workflow Chat",
  messages,
  onSendMessage,
  onMessageAction,
  isCreatingWorkflow = false,
  userName = "John D.",
  onToggleSidebar,
  isTyping = false
}: ChatMainProps) {
  const handleMessageAction = (messageId: number, action: string, value?: string) => {
    onMessageAction?.(messageId, action, value)
  }

  return (
    <div className="flex-1 flex flex-col h-full">
      {/* Enhanced Header */}
      <div className="border-b bg-white px-4 md:px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            {/* Mobile hamburger menu */}
            <Button
              variant="ghost"
              size="sm"
              className="p-2 md:hidden"
              onClick={onToggleSidebar}
            >
              <Menu className="h-4 w-4" />
            </Button>

            {/* Desktop back button */}
            <Button variant="ghost" size="sm" className="p-2 hidden md:flex">
              <ArrowLeft className="h-4 w-4" />
            </Button>

            <div className="flex items-center space-x-2">
              <Bot className="h-5 w-5 text-blue-600" />
              <span className="font-semibold text-gray-900 truncate">{chatTitle}</span>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <div className="flex items-center space-x-2">
              <Avatar className="w-6 h-6">
                <AvatarFallback className="text-xs">
                  {userName.split(' ').map(n => n[0]).join('')}
                </AvatarFallback>
              </Avatar>
              <span className="text-sm text-gray-600">{userName}</span>
            </div>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="p-2">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem>
                  <Settings className="h-4 w-4 mr-2" />
                  Chat Settings
                </DropdownMenuItem>
                <DropdownMenuItem>
                  Export Chat
                </DropdownMenuItem>
                <DropdownMenuItem className="text-red-600">
                  Clear History
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>

      {/* Welcome Message for Empty Chat */}
      {messages.length === 0 && (
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="text-center max-w-md">
            <Bot className="h-12 w-12 text-blue-600 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Welcome to Clixen AI!</h3>
            <p className="text-gray-600 mb-6">
              I can help you create powerful automation workflows using natural language.
              Just tell me what you'd like to automate, and I'll guide you through creating it step by step.
            </p>
            <div className="text-left space-y-2 text-sm text-gray-500">
              <p><strong>Try something like:</strong></p>
              <ul className="space-y-1 pl-4">
                <li>• "Send me an email every morning with the weather forecast"</li>
                <li>• "Backup my files to cloud storage weekly"</li>
                <li>• "Post daily motivational quotes to my social media"</li>
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* Chat Messages */}
      {messages.length > 0 && (
        <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-4 md:space-y-6">
          {messages.map((message) => (
            <div key={message.id}>
              <ChatMessage
                type={message.type}
                content={message.content}
                timestamp={message.timestamp}
                workflowSummary={message.workflowSummary}
                progress={message.progress}
                questions={message.questions}
                options={message.options}
                actionButtons={message.actionButtons}
                errorMessage={message.errorMessage}
                samplePreview={message.samplePreview}
                onAction={(action, value) => handleMessageAction(message.id, action, value)}
              />
              {message.showSuggestions && (
                <div className="mt-4">
                  <QuickSuggestions onSuggestionClick={onSendMessage} />
                </div>
              )}
            </div>
          ))}

          {/* Typing Indicator */}
          {isTyping && (
            <div className="mt-4">
              <TypingIndicator />
            </div>
          )}

          {/* Auto-scroll anchor */}
          <div id="chat-bottom" />
        </div>
      )}

      {/* Enhanced Input Area */}
      <div className="border-t bg-white p-4 md:p-6">
        <ChatInput
          onSendMessage={onSendMessage}
          disabled={isCreatingWorkflow || isTyping}
          placeholder={isCreatingWorkflow || isTyping ? "AI is working..." : "Describe your workflow..."}
        />
      </div>
    </div>
  )
}
