"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { TransitionManager, TransitionType } from "@/components/transitions/transition-manager"
import { ArrowLeft, Play, Loader2, Shield, Database, Bot } from "lucide-react"
import { useRouter } from "next/navigation"

export default function TransitionsDemo() {
  const router = useRouter()
  const [currentTransition, setCurrentTransition] = useState<TransitionType>(null)

  const transitions = [
    {
      id: "auth-loading",
      title: "Authentication Loading",
      description: "Landing → Authentication transition with animated progress",
      icon: Shield,
      color: "text-blue-600"
    },
    {
      id: "workflow-generation",
      title: "Workflow Generation",
      description: "Chat → AI workflow creation with step-by-step progress",
      icon: Bot,
      color: "text-purple-600"
    },
    {
      id: "dashboard-skeleton",
      title: "Dashboard Skeleton",
      description: "Dashboard loading state with skeleton UI",
      icon: Database,
      color: "text-green-600"
    },
    {
      id: "permission-dialog",
      title: "Permission Request",
      description: "Service integration permission dialog",
      icon: Shield,
      color: "text-orange-600"
    }
  ]

  const handleTransitionComplete = (result?: any) => {
    console.log("Transition completed:", result)
    setCurrentTransition(null)
  }

  const handleTransitionCancel = () => {
    console.log("Transition cancelled")
    setCurrentTransition(null)
  }

  return (
    <TransitionManager
      type={currentTransition}
      workflowType="social media automation"
      service="Google Drive"
      onComplete={handleTransitionComplete}
      onCancel={handleTransitionCancel}
    >
      <div className="min-h-screen bg-gradient-to-br from-blue-50 via-indigo-50 to-purple-50 p-6">
        <div className="max-w-4xl mx-auto space-y-8">
          {/* Header */}
          <div className="flex items-center space-x-4">
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.back()}
              className="flex items-center space-x-2"
            >
              <ArrowLeft className="h-4 w-4" />
              <span>Back</span>
            </Button>
            <div>
              <h1 className="text-3xl font-bold text-gray-900">Transition Screens & Loading States</h1>
              <p className="text-gray-600 mt-1">Magic UI effects and professional loading experiences</p>
            </div>
          </div>

          {/* Current Transition Status */}
          {currentTransition && (
            <Card className="border-2 border-blue-200 bg-blue-50">
              <CardContent className="p-4">
                <div className="flex items-center space-x-3">
                  <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
                  <span className="font-medium text-blue-800">
                    Running: {transitions.find(t => t.id === currentTransition)?.title}
                  </span>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Transition Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {transitions.map((transition) => {
              const TransitionIcon = transition.icon
              return (
                <Card key={transition.id} className="hover:shadow-lg transition-all duration-200">
                  <CardHeader>
                    <CardTitle className="flex items-center space-x-3">
                      <TransitionIcon className={`h-6 w-6 ${transition.color}`} />
                      <span className="text-lg">{transition.title}</span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <p className="text-gray-600 text-sm">
                      {transition.description}
                    </p>
                    
                    <Button
                      onClick={() => setCurrentTransition(transition.id as TransitionType)}
                      disabled={!!currentTransition}
                      className="w-full flex items-center space-x-2"
                    >
                      <Play className="h-4 w-4" />
                      <span>Preview Transition</span>
                    </Button>
                  </CardContent>
                </Card>
              )
            })}
          </div>

          {/* Implementation Notes */}
          <Card className="border-2 border-gray-200">
            <CardHeader>
              <CardTitle className="text-lg">Implementation Features</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-3">
                  <h4 className="font-semibold text-gray-900">✨ Magic UI Effects</h4>
                  <ul className="space-y-1 text-sm text-gray-600">
                    <li>• Animated progress bars with smooth transitions</li>
                    <li>• Pulsing dots and loading indicators</li>
                    <li>• Gradient backgrounds and backdrop blur</li>
                    <li>• Step-by-step status indicators</li>
                  </ul>
                </div>
                
                <div className="space-y-3">
                  <h4 className="font-semibold text-gray-900">🎯 Professional Loading</h4>
                  <ul className="space-y-1 text-sm text-gray-600">
                    <li>• Skeleton UI for content loading</li>
                    <li>• Real-time progress feedback</li>
                    <li>• Contextual status messages</li>
                    <li>• Estimated time remaining</li>
                  </ul>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Usage Instructions */}
          <Card className="bg-gray-50 border-2 border-dashed border-gray-300">
            <CardHeader>
              <CardTitle className="text-lg">How to Use in Your App</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="bg-gray-900 text-gray-100 p-4 rounded-lg text-sm font-mono overflow-x-auto">
                <div className="space-y-2">
                  <div className="text-green-400">// Import the TransitionManager</div>
                  <div>import {"{ TransitionManager }"} from "@/components/transitions/transition-manager"</div>
                  <div className="mt-4 text-green-400">// Use in your component</div>
                  <div>{"<TransitionManager"}</div>
                  <div>{"  type=\"workflow-generation\""}</div>
                  <div>{"  workflowType=\"email automation\""}</div>
                  <div>{"  onComplete={(result) => console.log(result)}"}</div>
                  <div>{">"}</div>
                  <div>{"  <YourContent />"}</div>
                  <div>{"</TransitionManager>"}</div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </TransitionManager>
  )
}
