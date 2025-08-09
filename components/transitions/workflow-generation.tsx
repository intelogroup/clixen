"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Badge } from "@/components/ui/badge"
import { Brain, Search, Building, Settings, Sparkles, Clock, CheckCircle, Loader2 } from "lucide-react"

interface WorkflowGenerationProps {
  workflowType?: string
  onComplete?: (workflowId: string) => void
}

export function WorkflowGeneration({ 
  workflowType = "social media automation",
  onComplete 
}: WorkflowGenerationProps) {
  const [currentStep, setCurrentStep] = useState(0)
  const [timeRemaining, setTimeRemaining] = useState(45)

  const steps = [
    {
      icon: Brain,
      title: "Understanding your request...",
      progress: 100,
      status: "completed" as const,
      duration: 2000
    },
    {
      icon: Search,
      title: "Analyzing platform APIs...",
      progress: 75,
      status: "in-progress" as const,
      duration: 3000
    },
    {
      icon: Building,
      title: "Building workflow structure...",
      progress: 35,
      status: "queued" as const,
      duration: 4000
    },
    {
      icon: Settings,
      title: "Configuring integrations...",
      progress: 0,
      status: "queued" as const,
      duration: 2000
    },
    {
      icon: Sparkles,
      title: "Adding Magic UI polish...",
      progress: 0,
      status: "queued" as const,
      duration: 1000
    }
  ]

  const [stepStates, setStepStates] = useState(steps)

  useEffect(() => {
    let stepIndex = 0
    const interval = setInterval(() => {
      if (stepIndex < steps.length) {
        setStepStates(prev => prev.map((step, index) => {
          if (index === stepIndex) {
            return { ...step, status: "in-progress" as const }
          } else if (index < stepIndex) {
            return { ...step, status: "completed" as const, progress: 100 }
          }
          return step
        }))

        setTimeout(() => {
          setStepStates(prev => prev.map((step, index) => {
            if (index === stepIndex) {
              return { ...step, status: "completed" as const, progress: 100 }
            }
            return step
          }))
          stepIndex++
        }, steps[stepIndex].duration)
      } else {
        clearInterval(interval)
        setTimeout(() => {
          onComplete?.("workflow-" + Date.now())
        }, 1000)
      }
    }, 500)

    const timeInterval = setInterval(() => {
      setTimeRemaining(prev => Math.max(0, prev - 1))
    }, 1000)

    return () => {
      clearInterval(interval)
      clearInterval(timeInterval)
    }
  }, [onComplete])

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "completed":
        return <CheckCircle className="h-4 w-4 text-green-600" />
      case "in-progress":
        return <Loader2 className="h-4 w-4 text-blue-600 animate-spin" />
      default:
        return <Clock className="h-4 w-4 text-gray-400" />
    }
  }

  const getStatusText = (status: string) => {
    switch (status) {
      case "completed":
        return "Complete"
      case "in-progress":
        return "In Progress"
      default:
        return "Queued"
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case "completed":
        return "bg-green-100 text-green-800"
      case "in-progress":
        return "bg-blue-100 text-blue-800"
      default:
        return "bg-gray-100 text-gray-600"
    }
  }

  return (
    <div className="fixed inset-0 bg-black/20 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl shadow-2xl">
        <CardHeader className="bg-gradient-to-r from-blue-50 to-purple-50">
          <CardTitle className="flex items-center space-x-2 text-xl">
            <Brain className="h-6 w-6 text-blue-600" />
            <span>AI WORKFLOW GENERATION</span>
          </CardTitle>
        </CardHeader>

        <CardContent className="p-6 space-y-6">
          {/* Status Message */}
          <div className="text-center">
            <p className="text-lg text-gray-700">
              Creating your <span className="font-semibold text-blue-600">{workflowType}</span> workflow...
            </p>
          </div>

          {/* Generation Progress */}
          <Card className="border-0 bg-gray-50">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-medium">GENERATION PROGRESS</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {stepStates.map((step, index) => {
                const StepIcon = step.icon
                return (
                  <div key={index} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-3">
                        <StepIcon className="h-5 w-5 text-gray-600" />
                        <span className="text-sm font-medium">{step.title}</span>
                      </div>
                      <div className="flex items-center space-x-2">
                        {getStatusIcon(step.status)}
                        <Badge className={getStatusColor(step.status)}>
                          {getStatusText(step.status)}
                        </Badge>
                      </div>
                    </div>
                    <Progress 
                      value={step.progress} 
                      className="h-2"
                    />
                    <div className="text-right text-xs text-muted-foreground">
                      {step.progress}%
                    </div>
                  </div>
                )
              })}
            </CardContent>
          </Card>

          {/* Fun Fact */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <div className="flex items-start space-x-2">
              <div className="text-blue-600 mt-0.5">💡</div>
              <p className="text-sm text-blue-800">
                <strong>Did you know?</strong> This workflow will save you ~2 hours per week!
              </p>
            </div>
          </div>

          {/* Time Remaining */}
          <Card className="border-dashed border-2 border-gray-300">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">ESTIMATED TIME REMAINING: {timeRemaining} seconds</span>
                <div className="flex items-center space-x-2">
                  <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                  <span className="text-sm text-muted-foreground">Analyzing...</span>
                </div>
              </div>
            </CardContent>
          </Card>
        </CardContent>
      </Card>
    </div>
  )
}
