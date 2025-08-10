'use client'

import { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { 
  X, 
  Home, 
  MessageCircle, 
  BarChart3, 
  Settings, 
  User,
  LogOut,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleDot,
  PauseCircle,
  Zap,
  Plus
} from 'lucide-react'

interface MobileSidebarProps {
  isOpen: boolean
  onClose: () => void
}

export function MobileSidebar({ isOpen, onClose }: MobileSidebarProps) {
  const pathname = usePathname()
  const [expandedWorkspaces, setExpandedWorkspaces] = useState<string[]>(['personal', 'business'])

  const toggleWorkspace = (workspace: string) => {
    setExpandedWorkspaces(prev => 
      prev.includes(workspace) 
        ? prev.filter(w => w !== workspace)
        : [...prev, workspace]
    )
  }

  const workspaces = [
    {
      id: 'personal',
      name: 'Personal',
      count: 4,
      workflows: [
        { name: 'Email Reports', status: 'active' },
        { name: 'Weather Alert', status: 'active' },
        { name: 'Data Backup', status: 'paused' },
        { name: 'News Digest', status: 'draft' }
      ]
    },
    {
      id: 'business',
      name: 'Business',
      count: 8,
      workflows: [
        { name: 'Lead Capture', status: 'active' },
        { name: 'CRM Sync', status: 'active' },
        { name: 'Invoicing', status: 'active' },
        { name: 'Team Reports', status: 'paused' },
        { name: 'Social Posts', status: 'draft' },
        { name: 'Survey Auto', status: 'draft' },
        { name: 'Support Tix', status: 'draft' },
        { name: 'Inventory', status: 'draft' }
      ]
    }
  ]

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'active': return <CircleDot className="w-3 h-3 text-green-500" />
      case 'paused': return <PauseCircle className="w-3 h-3 text-blue-500" />
      case 'draft': return <Circle className="w-3 h-3 text-gray-400" />
      default: return <Circle className="w-3 h-3 text-gray-400" />
    }
  }

  const navLinks = [
    { href: '/dashboard', icon: Home, label: 'Dashboard' },
    { href: '/chat', icon: MessageCircle, label: 'Chat' },
    { href: '/analytics', icon: BarChart3, label: 'Analytics' },
    { href: '/settings', icon: Settings, label: 'Settings' }
  ]

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 lg:hidden">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      
      {/* Sidebar */}
      <div className="absolute left-0 top-0 h-full w-80 bg-white border-r shadow-lg overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b">
          <div className="flex items-center space-x-2">
            <Zap className="w-6 h-6 text-blue-600" />
            <span className="font-bold text-lg">Clixen</span>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* New Chat Button */}
        <div className="p-4">
          <Link href="/chat">
            <Button className="w-full" onClick={onClose}>
              <Plus className="w-4 h-4 mr-2" />
              New Chat
            </Button>
          </Link>
        </div>

        {/* Navigation */}
        <div className="px-4 pb-4">
          <h3 className="text-sm font-medium text-gray-500 mb-2">Navigation</h3>
          <nav className="space-y-1">
            {navLinks.map((link) => (
              <Link 
                key={link.href}
                href={link.href}
                onClick={onClose}
                className={cn(
                  "flex items-center space-x-2 px-2 py-2 rounded-lg text-sm transition-colors",
                  pathname === link.href 
                    ? "bg-blue-50 text-blue-700" 
                    : "text-gray-700 hover:bg-gray-100"
                )}
              >
                <link.icon className="w-4 h-4" />
                <span>{link.label}</span>
              </Link>
            ))}
          </nav>
        </div>

        {/* Workspaces */}
        <div className="px-4 pb-4">
          <h3 className="text-sm font-medium text-gray-500 mb-2">Workspaces</h3>
          <div className="space-y-2">
            {workspaces.map((workspace) => (
              <div key={workspace.id}>
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full justify-start p-2 h-auto"
                  onClick={() => toggleWorkspace(workspace.id)}
                >
                  {expandedWorkspaces.includes(workspace.id) ? (
                    <ChevronDown className="w-3 h-3 mr-1" />
                  ) : (
                    <ChevronRight className="w-3 h-3 mr-1" />
                  )}
                  <span className="text-sm font-medium">
                    {workspace.name} ({workspace.count})
                  </span>
                </Button>
                
                {expandedWorkspaces.includes(workspace.id) && (
                  <div className="ml-4 space-y-1">
                    {workspace.workflows.map((workflow, index) => (
                      <div key={index} className="flex items-center space-x-2 py-1 px-2 text-xs text-gray-600">
                        {getStatusIcon(workflow.status)}
                        <span>{workflow.name}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Profile Section */}
        <div className="mt-auto border-t p-4">
          <div className="flex items-center space-x-2 mb-2">
            <User className="w-4 h-4 text-gray-600" />
            <span className="text-sm text-gray-700">Profile</span>
          </div>
          <Button variant="ghost" size="sm" className="w-full justify-start text-red-600 hover:text-red-700 hover:bg-red-50">
            <LogOut className="w-4 h-4 mr-2" />
            Sign Out
          </Button>
        </div>
      </div>
    </div>
  )
}