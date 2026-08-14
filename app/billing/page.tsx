"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { useCurrentUser, useAuthActions } from "@/lib/auth-context"
import { RequireAuth } from "@/components/auth/require-auth"
import { DashboardSidebar } from "@/components/dashboard/dashboard-sidebar"
import { MobileSidebar } from "@/components/ui/mobile-sidebar"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { User, Bot, Cpu, Server, HardDrive, RefreshCw, Shield, CheckCircle2, XCircle } from "lucide-react"

export default function RuntimePage() {
  const router = useRouter()
  const user = useCurrentUser()
  const { signOut } = useAuthActions()
  const [ollama, setOllama] = useState<{ status?: string; models?: string[] } | null>(null)
  const [loading, setLoading] = useState(true)

  const loadHealth = () => {
    setLoading(true)
    fetch("/api/runtime")
      .then(async (res) => {
        if (!res.ok) throw new Error(`Backend returned ${res.status}`)
        setOllama(await res.json())
      })
      .catch(() => setOllama({ status: "error", models: [] }))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadHealth()
  }, [])

  const handleSignOut = async () => {
    await signOut()
    router.push('/auth/signin')
  }

  const healthOk = ollama?.status === "ok"

  return (
    <RequireAuth>
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-purple-50 flex">
        <MobileSidebar onSignOut={handleSignOut} />

        <div className="hidden md:block relative">
          <DashboardSidebar onSignOut={handleSignOut} workflows={[]} />
        </div>

        <div className="flex-1 min-w-0 flex flex-col h-screen">
          {/* Header */}
          <div className="bg-white/80 backdrop-blur-sm border-b border-white/20 px-6 py-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-4">
                <Bot className="h-6 w-6 text-blue-600" />
                <h1 className="text-2xl font-bold bg-gradient-to-r from-slate-900 to-slate-700 bg-clip-text text-transparent">
                  Local Runtime
                </h1>
              </div>

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm" className="hover:bg-slate-100 rounded-xl transition-all duration-200">
                    <Avatar className="w-8 h-8 ring-2 ring-slate-200">
                      <AvatarFallback className="text-sm font-semibold bg-gradient-to-br from-blue-500 to-purple-600 text-white">
                        {user?.firstName?.[0]}{user?.lastName?.[0]}
                      </AvatarFallback>
                    </Avatar>
                    <span className="ml-3 hidden sm:inline font-medium text-slate-700">Profile</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-48 rounded-xl border-slate-200 shadow-xl">
                  <DropdownMenuItem onClick={() => router.push('/profile')} className="rounded-lg">
                    <User className="h-4 w-4 mr-2" />
                    View Profile
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={handleSignOut} className="text-red-600 rounded-lg">
                    Sign Out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-4xl mx-auto space-y-6">
              {/* Intro */}
              <div className="mb-2">
                <h2 className="text-lg font-semibold text-slate-900">Your Clixen runs entirely on this machine</h2>
                <p className="text-sm text-slate-600 mt-1">
                  No subscription, no credit card, no cloud bill. Local models and tools live here — this page shows what's available right now.
                </p>
              </div>

              {/* Ollama health */}
              <Card className="bg-white/80 backdrop-blur-sm border-slate-200 shadow-sm">
                <CardContent className="p-6">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center space-x-3">
                      <div className="p-2.5 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600">
                        <Cpu className="h-5 w-5 text-white" />
                      </div>
                      <div>
                        <h3 className="font-semibold text-slate-900">Local models (Ollama)</h3>
                        <p className="text-xs text-slate-500">Inference engine for local agents</p>
                      </div>
                    </div>
                    <Button variant="ghost" size="sm" onClick={loadHealth} disabled={loading}>
                      <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
                      Refresh
                    </Button>
                  </div>

                  {loading ? (
                    <div className="flex items-center gap-2 text-slate-500 py-4">
                      <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce"></div>
                      <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style={{animationDelay: '0.1s'}}></div>
                      <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style={{animationDelay: '0.2s'}}></div>
                      <span className="text-sm">Checking model status...</span>
                    </div>
                  ) : healthOk ? (
                    <div>
                      <div className="flex items-center gap-2 text-emerald-600 text-sm font-medium mb-3">
                        <CheckCircle2 className="h-4 w-4" />
                        Engine healthy
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {(ollama?.models ?? []).map((m) => (
                          <span key={m} className="px-3 py-1.5 rounded-lg bg-slate-100 border border-slate-200 text-sm font-mono text-slate-700">
                            {m}
                          </span>
                        ))}
                        {(ollama?.models?.length ?? 0) === 0 && (
                          <span className="text-sm text-slate-500">No models pulled yet.</span>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 text-rose-600 text-sm font-medium">
                      <XCircle className="h-4 w-4" />
                      Ollama is not reachable. Local agents will fall back to cloud models.
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Account */}
              <Card className="bg-white/80 backdrop-blur-sm border-slate-200 shadow-sm">
                <CardContent className="p-6">
                  <div className="flex items-center space-x-3 mb-4">
                    <div className="p-2.5 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600">
                      <Shield className="h-5 w-5 text-white" />
                    </div>
                    <div>
                      <h3 className="font-semibold text-slate-900">Workspace</h3>
                      <p className="text-xs text-slate-500">Single-user local account</p>
                    </div>
                  </div>
                  <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                    <div className="bg-slate-50 border border-slate-100 rounded-lg p-3">
                      <dt className="text-xs text-slate-500 mb-1">Account</dt>
                      <dd className="font-medium text-slate-900">{user?.email}</dd>
                    </div>
                    <div className="bg-slate-50 border border-slate-100 rounded-lg p-3">
                      <dt className="text-xs text-slate-500 mb-1">Role</dt>
                      <dd className="font-medium text-slate-900">{user?.role || "Owner"}</dd>
                    </div>
                    <div className="bg-slate-50 border border-slate-100 rounded-lg p-3">
                      <dt className="text-xs text-slate-500 mb-1">Workspace</dt>
                      <dd className="font-medium text-slate-900">{user?.workspaceName || "Local Workspace"}</dd>
                    </div>
                    <div className="bg-slate-50 border border-slate-100 rounded-lg p-3">
                      <dt className="text-xs text-slate-500 mb-1">Billing</dt>
                      <dd className="font-medium text-emerald-700">None — runs locally</dd>
                    </div>
                  </dl>
                </CardContent>
              </Card>

              {/* Runtime facts */}
              <Card className="bg-white/80 backdrop-blur-sm border-slate-200 shadow-sm">
                <CardContent className="p-6">
                  <div className="flex items-center space-x-3 mb-4">
                    <div className="p-2.5 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600">
                      <Server className="h-5 w-5 text-white" />
                    </div>
                    <div>
                      <h3 className="font-semibold text-slate-900">Runtime</h3>
                      <p className="text-xs text-slate-500">Where your data lives</p>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                    <div className="bg-slate-50 border border-slate-100 rounded-lg p-3 flex items-start gap-3">
                      <HardDrive className="h-4 w-4 text-slate-400 mt-0.5" />
                      <div>
                        <div className="font-medium text-slate-900">Local storage</div>
                        <div className="text-xs text-slate-500 mt-0.5">Files, chat history, and workflows are stored on this machine only.</div>
                      </div>
                    </div>
                    <div className="bg-slate-50 border border-slate-100 rounded-lg p-3 flex items-start gap-3">
                      <Bot className="h-4 w-4 text-slate-400 mt-0.5" />
                      <div>
                        <div className="font-medium text-slate-900">AI assistants</div>
                        <div className="text-xs text-slate-500 mt-0.5">Local models when available, cloud fallback for heavy reasoning.</div>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      </div>
    </RequireAuth>
  )
}
