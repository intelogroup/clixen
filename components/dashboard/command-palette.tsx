"use client"

import { useState, useEffect } from "react"
import { Search, Zap, Plus, Settings, BarChart3, Users, Database, Cloud } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

interface Command {
  id: string
  title: string
  description: string
  icon: any
  shortcut: string
  category: "create" | "manage" | "analytics" | "settings"
  action: () => void
}

interface DashboardCommandPaletteProps {
  isOpen: boolean
  onClose: () => void
  onCreateWorkflow: () => void
  onNavigate: (path: string) => void
}

export function DashboardCommandPalette({
  isOpen,
  onClose,
  onCreateWorkflow,
  onNavigate
}: DashboardCommandPaletteProps) {
  const [query, setQuery] = useState("")
  const [selectedIndex, setSelectedIndex] = useState(0)

  const commands: Command[] = [
    {
      id: "new-workflow",
      title: "Create New Workflow",
      description: "Start building a new automation",
      icon: Plus,
      shortcut: "⌘N",
      category: "create",
      action: onCreateWorkflow
    },
    {
      id: "social-automation",
      title: "Social Media Automation",
      description: "Automate your social media posting",
      icon: Users,
      shortcut: "⌘S",
      category: "create",
      action: () => onCreateWorkflow()
    },
    {
      id: "email-campaigns",
      title: "Email Campaigns",
      description: "Set up automated email workflows",
      icon: Cloud,
      shortcut: "⌘E",
      category: "create",
      action: () => onCreateWorkflow()
    },
    {
      id: "data-backup",
      title: "Data Backup",
      description: "Automated backup workflows",
      icon: Database,
      shortcut: "⌘B",
      category: "create",
      action: () => onCreateWorkflow()
    },
    {
      id: "analytics",
      title: "View Analytics",
      description: "Check your workflow performance",
      icon: BarChart3,
      shortcut: "⌘A",
      category: "analytics",
      action: () => onNavigate("/analytics")
    },
    {
      id: "settings",
      title: "Settings",
      description: "Manage your account and preferences",
      icon: Settings,
      shortcut: "⌘,",
      category: "settings",
      action: () => onNavigate("/settings")
    }
  ]

  const filteredCommands = commands.filter(cmd =>
    cmd.title.toLowerCase().includes(query.toLowerCase()) ||
    cmd.description.toLowerCase().includes(query.toLowerCase()) ||
    cmd.category.toLowerCase().includes(query.toLowerCase())
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
            filteredCommands[selectedIndex].action()
            onClose()
          }
          break
      }
    }

    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [isOpen, filteredCommands, selectedIndex, onClose])

  // Global keyboard shortcut
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault()
        if (!isOpen) {
          // Open command palette
        }
      }
    }

    document.addEventListener("keydown", handleGlobalKeyDown)
    return () => document.removeEventListener("keydown", handleGlobalKeyDown)
  }, [isOpen])

  if (!isOpen) return null

  const getCategoryColor = (category: string) => {
    switch (category) {
      case "create":
        return "bg-blue-100 text-blue-800"
      case "manage":
        return "bg-green-100 text-green-800"
      case "analytics":
        return "bg-purple-100 text-purple-800"
      case "settings":
        return "bg-gray-100 text-gray-800"
      default:
        return "bg-gray-100 text-gray-800"
    }
  }

  return (
    <div className="fixed inset-0 bg-black/20 z-50 flex items-start justify-center pt-[15vh]">
      <Card className="w-full max-w-2xl mx-4 animate-in fade-in slide-in-from-top-4 duration-200 shadow-2xl">
        <CardHeader className="pb-3 border-b">
          <CardTitle className="flex items-center space-x-2 text-lg">
            <Zap className="h-5 w-5 text-yellow-500" />
            <span>Command Palette</span>
            <Badge variant="secondary" className="text-xs">⌘K</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {/* Search Input */}
          <div className="relative p-4 border-b">
            <Search className="absolute left-7 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search commands... (try 'create', 'social', 'email')"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setSelectedIndex(0)
              }}
              className="pl-10 border-0 focus-visible:ring-0 text-base"
              autoFocus
            />
          </div>

          {/* Commands List */}
          <div className="max-h-96 overflow-y-auto">
            {filteredCommands.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <Search className="h-8 w-8 mx-auto mb-3 opacity-50" />
                <p>No commands found</p>
                <p className="text-sm mt-1">Try searching for 'create' or 'workflow'</p>
              </div>
            ) : (
              <div className="p-2">
                {filteredCommands.map((cmd, index) => {
                  const CommandIcon = cmd.icon
                  return (
                    <Button
                      key={cmd.id}
                      variant="ghost"
                      className={cn(
                        "w-full justify-start h-auto p-4 mb-1",
                        index === selectedIndex && "bg-accent"
                      )}
                      onClick={() => {
                        cmd.action()
                        onClose()
                      }}
                    >
                      <CommandIcon className="h-5 w-5 mr-3 text-gray-600" />
                      <div className="text-left flex-1">
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-medium">{cmd.title}</span>
                          <div className="flex items-center space-x-2">
                            <Badge className={getCategoryColor(cmd.category)} variant="secondary">
                              {cmd.category}
                            </Badge>
                            <Badge variant="outline" className="text-xs font-mono">
                              {cmd.shortcut}
                            </Badge>
                          </div>
                        </div>
                        <p className="text-sm text-muted-foreground">{cmd.description}</p>
                      </div>
                    </Button>
                  )
                })}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="border-t p-3 bg-gray-50">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <div className="flex items-center space-x-4">
                <div className="flex items-center space-x-1">
                  <kbd className="px-1.5 py-0.5 rounded bg-white border text-xs">↑↓</kbd>
                  <span>Navigate</span>
                </div>
                <div className="flex items-center space-x-1">
                  <kbd className="px-1.5 py-0.5 rounded bg-white border text-xs">Enter</kbd>
                  <span>Select</span>
                </div>
                <div className="flex items-center space-x-1">
                  <kbd className="px-1.5 py-0.5 rounded bg-white border text-xs">Esc</kbd>
                  <span>Close</span>
                </div>
              </div>
              <div className="text-right">
                {filteredCommands.length} commands
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
