"use client"

import { useState } from "react"
import { Send, Paperclip, Mic, Sparkles, Link, Database, Command } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent } from "@/components/ui/card"
import { CommandPalette } from "./command-palette"

interface ChatInputProps {
  onSendMessage: (message: string) => void
  disabled?: boolean
  placeholder?: string
}

export function ChatInput({
  onSendMessage,
  disabled = false,
  placeholder = "Tell me about your workflow..."
}: ChatInputProps) {
  const [message, setMessage] = useState("")
  const [showCommands, setShowCommands] = useState(false)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (message.trim() && !disabled) {
      onSendMessage(message.trim())
      setMessage("")
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "/" && !message.trim()) {
      e.preventDefault()
      setShowCommands(true)
    }
  }

  const handleCommand = (command: string) => {
    const templates = {
      "/weather": "Create a daily weather notification workflow that sends me updates every morning at 8 AM",
      "/email": "Set up an automated email campaign for new subscribers with a welcome series",
      "/social": "Automate my social media posting across Facebook, Twitter, and LinkedIn",
      "/backup": "Create a weekly backup workflow for my important files to cloud storage",
      "/crm": "Integrate my CRM to automatically add new leads and send follow-up emails"
    }

    const template = templates[command as keyof typeof templates]
    if (template) {
      setMessage(template)
    }
  }

  const exampleSuggestions = [
    "Post daily motivational quotes to Instagram and LinkedIn",
    "Backup my database to cloud storage every week",
    "Send me email alerts when my website goes down"
  ]

  return (
    <>
      <CommandPalette
        isOpen={showCommands}
        onClose={() => setShowCommands(false)}
        onCommand={handleCommand}
      />

      <Card className="border-2">
        <CardContent className="p-6">
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-gray-700 mb-2 block">
                Describe your workflow
              </label>
              <form onSubmit={handleSubmit} className="space-y-4">
                <Textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={disabled ? "AI is creating your workflow..." : "Type here... (or drag & drop files)"}
                  disabled={disabled}
                  className="min-h-[100px] resize-none"
                />

                {/* Rich Input Controls */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={disabled}
                      className="h-8"
                    >
                      <Paperclip className="h-3 w-3 mr-1" />
                      <span className="text-xs">Attach</span>
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={disabled}
                      className="h-8"
                    >
                      <Mic className="h-3 w-3 mr-1" />
                      <span className="text-xs">Voice</span>
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={disabled}
                      className="h-8"
                    >
                      <Sparkles className="h-3 w-3 mr-1" />
                      <span className="text-xs">Templates</span>
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={disabled}
                      className="h-8"
                      onClick={() => setShowCommands(true)}
                    >
                      <Command className="h-3 w-3 mr-1" />
                      <span className="text-xs">Commands</span>
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={disabled}
                      className="h-8"
                    >
                      <Link className="h-3 w-3 mr-1" />
                      <span className="text-xs">URL</span>
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={disabled}
                      className="h-8"
                    >
                      <Database className="h-3 w-3 mr-1" />
                      <span className="text-xs">Data</span>
                    </Button>
                  </div>

                  <Button
                    type="submit"
                    disabled={!message.trim() || disabled}
                    className="h-8"
                  >
                    <Send className="h-3 w-3 mr-1" />
                    <span className="text-xs">Send</span>
                  </Button>
                </div>
              </form>
            </div>

            {/* Example Suggestions */}
            <div className="pt-2 border-t">
              <p className="text-xs text-muted-foreground mb-2">
                Try these examples or press{" "}
                <kbd className="px-1.5 py-0.5 rounded bg-muted font-mono text-xs">/</kbd>{" "}
                for magic commands:
              </p>
              <div className="flex flex-wrap gap-2">
                {exampleSuggestions.map((suggestion, index) => (
                  <Button
                    key={index}
                    variant="ghost"
                    size="sm"
                    className="h-auto py-1 px-2 text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => setMessage(suggestion)}
                    disabled={disabled}
                  >
                    "{suggestion}"
                  </Button>
                ))}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </>
  )
}
