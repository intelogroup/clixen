"use client"

import { useState } from "react"
import { Send, Paperclip, Mic, Sparkles, Link, Database } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent } from "@/components/ui/card"

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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (message.trim() && !disabled) {
      onSendMessage(message.trim())
      setMessage("")
    }
  }

  const exampleSuggestions = [
    "Post daily motivational quotes to Instagram and LinkedIn",
    "Backup my database to cloud storage every week",
    "Send me email alerts when my website goes down"
  ]

  return (
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
            <p className="text-xs text-muted-foreground mb-2">Try these examples:</p>
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
  )
}
