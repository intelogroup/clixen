"use client"

import { Plus, Search, Filter, User, Settings, LogOut } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"

interface DashboardHeaderProps {
  workflowCount: number
  onCreateWorkflow?: () => void
  onSearch?: (query: string) => void
}

export function DashboardHeader({ workflowCount, onCreateWorkflow, onSearch }: DashboardHeaderProps) {
  return (
    <div className="border-b bg-white">
      <div className="container mx-auto px-4 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-6">
            <div className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full" />
              <h1 className="text-xl font-bold">Clixen</h1>
              <span className="text-sm text-muted-foreground">Workflow Automation</span>
            </div>
          </div>
          
          <div className="flex items-center space-x-4">
            <Button onClick={onCreateWorkflow} className="flex items-center space-x-2">
              <Plus className="h-4 w-4" />
              <span>Create Workflow</span>
            </Button>
            
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="h-8 w-8 p-0">
                  <Avatar className="h-7 w-7">
                    <AvatarFallback>
                      <User className="h-4 w-4" />
                    </AvatarFallback>
                  </Avatar>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuItem>
                  <User className="h-4 w-4 mr-2" />
                  Profile
                </DropdownMenuItem>
                <DropdownMenuItem>
                  <Settings className="h-4 w-4 mr-2" />
                  Settings
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem>
                  <LogOut className="h-4 w-4 mr-2" />
                  Sign Out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        
        <div className="flex items-center justify-between mt-6">
          <div>
            <h2 className="text-2xl font-bold">Your Workflows</h2>
            <p className="text-muted-foreground">
              {workflowCount === 0 
                ? "Create your first automated workflow" 
                : `${workflowCount} workflow${workflowCount === 1 ? '' : 's'} ready to automate your tasks`
              }
            </p>
          </div>
          
          <div className="flex items-center space-x-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search workflows..."
                className="pl-10 w-64"
                onChange={(e) => onSearch?.(e.target.value)}
              />
            </div>
            <Button variant="outline" size="sm">
              <Filter className="h-4 w-4 mr-1" />
              Filter
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
