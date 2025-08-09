"use client"

import { useState, useEffect } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Rocket, Shield, Zap } from "lucide-react"

interface AuthLoadingProps {
  onComplete?: () => void
}

export function AuthLoading({ onComplete }: AuthLoadingProps) {
  const [progress, setProgress] = useState(0)
  const [currentStep, setCurrentStep] = useState(0)

  const steps = [
    { icon: Rocket, text: "Preparing your workspace", delay: 1000 },
    { icon: Shield, text: "Securing your session", delay: 1500 },
    { icon: Zap, text: "Connecting to AI engine", delay: 2000 }
  ]

  useEffect(() => {
    const interval = setInterval(() => {
      setProgress(prev => {
        if (prev >= 100) {
          clearInterval(interval)
          setTimeout(() => onComplete?.(), 500)
          return 100
        }
        return prev + 2
      })
    }, 50)

    const stepInterval = setInterval(() => {
      setCurrentStep(prev => (prev + 1) % steps.length)
    }, 1000)

    return () => {
      clearInterval(interval)
      clearInterval(stepInterval)
    }
  }, [onComplete])

  return (
    <div className="fixed inset-0 bg-gradient-to-br from-blue-50 via-indigo-50 to-purple-50 flex items-center justify-center z-50">
      <div className="text-center space-y-8 max-w-md mx-auto px-6">
        {/* Header */}
        <div className="space-y-4">
          <div className="text-6xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-blue-600 to-purple-600 animate-pulse">
            ✨ TRANSITIONING ✨
          </div>
        </div>

        {/* Main Card */}
        <Card className="w-full shadow-2xl border-0 bg-white/80 backdrop-blur-sm">
          <CardContent className="p-8 space-y-6">
            {/* Logo */}
            <div className="text-center space-y-2">
              <div className="text-2xl font-bold text-gray-900 flex items-center justify-center space-x-2">
                <span className="text-yellow-500">⭐</span>
                <span>CLIXEN AI</span>
              </div>
            </div>

            {/* Progress Bar */}
            <div className="space-y-2">
              <Progress value={progress} className="h-3" />
              <div className="text-sm text-gray-600 text-center">Loading... {Math.round(progress)}%</div>
            </div>

            {/* Status Messages */}
            <div className="space-y-3">
              {steps.map((step, index) => {
                const StepIcon = step.icon
                return (
                  <div
                    key={index}
                    className={`flex items-center space-x-3 transition-all duration-300 ${
                      index <= currentStep ? 'opacity-100' : 'opacity-40'
                    }`}
                  >
                    <StepIcon className={`h-5 w-5 ${
                      index === currentStep ? 'text-blue-600 animate-pulse' : 
                      index < currentStep ? 'text-green-600' : 'text-gray-400'
                    }`} />
                    <span className="text-sm">{step.text}</span>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>

        {/* Animated Dots */}
        <div className="flex justify-center space-x-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="w-2 h-2 bg-blue-400 rounded-full animate-bounce"
              style={{
                animationDelay: `${i * 0.2}s`,
                animationDuration: '1s'
              }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
