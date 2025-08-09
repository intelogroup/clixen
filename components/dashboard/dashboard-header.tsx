"use client"

import { Plus, Search, Filter, User, Settings, LogOut, Bot } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { useAuthActions } from "@/lib/auth-context"
import { useRouter } from "next/navigation"

interface User {
  name: string
  email: string
  avatar?: string
  plan: "free" | "pro" | "enterprise"
}

interface DashboardHeaderProps {
  user: User
  onSearch?: (query: string) => void
}

export function DashboardHeader({ user, onSearch }: DashboardHeaderProps) {
  const { signOut } = useAuthActions()
  const router = useRouter()

  const handleSignOut = async () => {
    await signOut()
    router.push('/')
  }

  const getPlanColor = (plan: string) => {
    switch (plan) {
      case "pro": return "bg-blue-100 text-blue-800"
      case "enterprise": return "bg-purple-100 text-purple-800"
      default: return "bg-gray-100 text-gray-800"
    }
  }

  return (
    <div className="border-b bg-white sticky top-0 z-40">
      <div className="container mx-auto px-4 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-6">
            <div className="flex items-center space-x-2">
              <Bot className="h-6 w-6 text-blue-600" />
              <h1 className="text-xl font-bold text-gray-900">Clixen</h1>
              <span className="text-sm text-muted-foreground">Dashboard</span>
            </div>
          </div>
          
          <div className="flex items-center space-x-4">
            {/* Create Workflow Button */}
            <Button 
              onClick={() => router.push('/chat')} 
              className="bg-blue-600 hover:bg-blue-700"
            >
              <Plus className="h-4 w-4 mr-2" />
              Create Workflow
            </Button>
            
            {/* User Menu */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="h-auto p-2 space-x-2">
                  <Avatar className="h-8 w-8">
                    {user.avatar ? (
                      <AvatarImage src={user.avatar} alt={user.name} />
                    ) : (
                      <AvatarFallback className="bg-blue-100 text-blue-600">
                        {user.name.charAt(0).toUpperCase()}
                      </AvatarFallback>
                    )}
                  </Avatar>
                  <div className="text-left hidden sm:block">
                    <div className="text-sm font-medium">{user.name}</div>
                    <div className="text-xs text-gray-500">{user.email}</div>
                  </div>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <div className="px-2 py-1.5">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium">{user.name}</p>
                      <p className="text-xs text-gray-500">{user.email}</p>
                    </div>
                    <Badge className={getPlanColor(user.plan)}>
                      {user.plan}
                    </Badge>
                  </div>
                </div>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => router.push('/profile')}>
                  <User className="h-4 w-4 mr-2" />
                  Profile
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => router.push('/billing')}>
                  <Settings className="h-4 w-4 mr-2" />
                  Billing & Settings
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleSignOut} className="text-red-600">
                  <LogOut className="h-4 w-4 mr-2" />
                  Sign Out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>
    </div>
  )
}
