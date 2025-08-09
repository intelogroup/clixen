"use client"

import { useState, useEffect } from "react"
import { Search, Cloud, Mail, Share2, Database, Users, Zap } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

interface CommandPaletteProps {
  isOpen: boolean
  onClose: () => void
  onCommand: (command: string) => void
}

export function CommandPalette({ isOpen, onClose, onCommand }: CommandPaletteProps) {
  const [query, setQuery] = useState("")
  const [selectedIndex, setSelectedIndex] = useState(0)

  const commands = [
    {
      command: "/weather",
      description: "Weather workflow",
      icon: Cloud,
      color: "text-blue-600",
      example: "Daily weather notifications"
    },
    {
      command: "/email",
      description: "Email automation",
      icon: Mail,
      color: "text-green-600",
      example: "Automated email campaigns"
    },
    {
      command: "/social",
      description: "Social media",
      icon: Share2,
      color: "text-purple-600",
      example: "Cross-platform posting"
    },
    {
      command: "/backup",
      description: "Data backup",
      icon: Database,
      color: "text-orange-600",
      example: "Scheduled data protection"
    },
    {
      command: "/crm",
      description: "CRM integration",
      icon: Users,
      color: "text-indigo-600",
      example: "Customer management flows"
    }
  ]

  const filteredCommands = commands.filter(cmd => 
    cmd.command.toLowerCase().includes(query.toLowerCase()) ||
    cmd.description.toLowerCase().includes(query.toLowerCase())
  )

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!isOpen) return

      switch (e.key) {
        case "Escape":
          onClose()
          break
        case "ArrowDown":
          e.preventDefault()
          setSelectedIndex(prev => 
            prev < filteredCommands.length - 1 ? prev + 1 : 0
          )
          break
        case "ArrowUp":
          e.preventDefault()
          setSelectedIndex(prev => 
            prev > 0 ? prev - 1 : filteredCommands.length - 1
          )
          break
        case "Enter":
          e.preventDefault()
          if (filteredCommands[selectedIndex]) {
            handleCommand(filteredCommands[selectedIndex].command)
          }
          break
      }
    }

    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [isOpen, filteredCommands, selectedIndex])

  const handleCommand = (command: string) => {
    onCommand(command)
    onClose()
    setQuery("")
    setSelectedIndex(0)
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/20 z-50 flex items-start justify-center pt-[20vh]">
      <Card className="w-full max-w-lg mx-4 animate-in fade-in slide-in-from-top-4 duration-200">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center space-x-2 text-lg">
            <Zap className="h-5 w-5 text-yellow-500" />
            <span>Magic Commands</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search commands..."
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setSelectedIndex(0)
              }}
              className="pl-10"
              autoFocus
            />
          </div>

          <div className="space-y-1 max-h-64 overflow-y-auto">
            {filteredCommands.map((cmd, index) => (
              <Button
                key={cmd.command}
                variant="ghost"
                className={cn(
                  "w-full justify-start h-auto p-3",
                  index === selectedIndex && "bg-accent"
                )}
                onClick={() => handleCommand(cmd.command)}
              >
                <cmd.icon className={cn("h-4 w-4 mr-3", cmd.color)} />
                <div className="text-left flex-1">
                  <div className="flex items-center space-x-2 mb-1">
                    <Badge variant="secondary" className="text-xs font-mono">
                      {cmd.command}
                    </Badge>
                    <span className="text-sm font-medium">{cmd.description}</span>
                  </div>
                  <div className="text-xs text-muted-foreground">{cmd.example}</div>
                </div>
              </Button>
            ))}
          </div>

          {filteredCommands.length === 0 && (
            <div className="text-center py-6 text-muted-foreground">
              <Search className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-sm">No commands found</p>
            </div>
          )}

          <div className="border-t pt-3">
            <div className="text-xs text-muted-foreground">
              <kbd className="px-1.5 py-0.5 rounded bg-muted font-mono">↑↓</kbd> Navigate •{" "}
              <kbd className="px-1.5 py-0.5 rounded bg-muted font-mono">Enter</kbd> Select •{" "}
              <kbd className="px-1.5 py-0.5 rounded bg-muted font-mono">Esc</kbd> Close
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
