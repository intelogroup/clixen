"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Bot, LogOut, Menu, X } from "lucide-react"
import { Button } from "@/components/ui/button"

interface MobileSidebarProps {
  onSignOut?: () => void
}

export function MobileSidebar({ onSignOut }: MobileSidebarProps) {
  const router = useRouter()
  const [open, setOpen] = useState(false)

  const handleNavigate = (route: string) => {
    setOpen(false)
    router.push(route)
  }

  return (
    <>
      {/* Mobile header bar */}
      <div className="md:hidden flex items-center justify-between px-4 py-3 bg-white/80 backdrop-blur-sm border-b border-white/20 fixed top-0 left-0 right-0 z-50">
        <button
          onClick={() => setOpen(true)}
          className="p-2 rounded-lg hover:bg-slate-100 transition-colors"
          aria-label="Open menu"
        >
          <Menu className="h-5 w-5 text-slate-700" />
        </button>
        <div className="flex items-center space-x-2">
          <Bot className="h-5 w-5 text-blue-600" />
          <span className="text-sm font-semibold text-slate-900">Clixen</span>
        </div>
      </div>

      {/* Slide-over drawer */}
      {open && (
        <div className="md:hidden fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={() => setOpen(false)}
          />
          <div className="absolute left-0 top-0 h-full w-72 bg-white shadow-2xl flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-slate-100">
              <div className="flex items-center space-x-2">
                <Bot className="h-6 w-6 text-blue-600" />
                <span className="text-lg font-bold text-slate-900">Clixen</span>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="p-2 rounded-lg hover:bg-slate-100 transition-colors"
                aria-label="Close menu"
              >
                <X className="h-5 w-5 text-slate-700" />
              </button>
            </div>

            <nav className="flex-1 p-4 space-y-1">
              <button
                onClick={() => handleNavigate("/dashboard")}
                className="w-full text-left px-4 py-3 rounded-xl text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
              >
                Dashboard
              </button>
              <button
                onClick={() => handleNavigate("/chat")}
                className="w-full text-left px-4 py-3 rounded-xl text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
              >
                Create Workflow
              </button>
              <button
                onClick={() => handleNavigate("/billing")}
                className="w-full text-left px-4 py-3 rounded-xl text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
              >
                Billing & Settings
              </button>
            </nav>

            <div className="p-4 border-t border-slate-100">
              {onSignOut && (
                <Button
                  variant="outline"
                  className="w-full text-red-600 border-red-200 hover:bg-red-50"
                  onClick={() => {
                    setOpen(false)
                    onSignOut()
                  }}
                >
                  <LogOut className="h-4 w-4 mr-2" />
                  Sign Out
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
