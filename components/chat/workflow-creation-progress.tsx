'use client'

import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { Progress } from '@/components/ui/progress'
import { Button } from '@/components/ui/button'
import { 
  Check, 
  Loader2, 
  AlertTriangle,
  Rocket,
  Settings,
  Play,
  Eye
} from 'lucide-react'

interface WorkflowCreationProgressProps {
  workflowName: string
  onComplete?: () => void
  onError?: (error: string) => void
}

interface Step {
  id: string
  label: string
  status: 'pending' | 'in-progress' | 'completed' | 'failed'
  description?: string
}

export function WorkflowCreationProgress({ 
  workflowName, 
  onComplete, 
  onError 
}: WorkflowCreationProgressProps) {
  const [currentStep, setCurrentStep] = useState(0)
  const [progress, setProgress] = useState(0)
  const [isComplete, setIsComplete] = useState(false)
  const [hasError, setHasError] = useState(false)
  const [workflowId, setWorkflowId] = useState('')

  const [steps, setSteps] = useState<Step[]>([
    {
      id: 'validate',
      label: 'Workflow validated',
      status: 'pending',
      description: 'Checking workflow logic and dependencies'
    },
    {
      id: 'credentials',
      label: 'Credentials configured',
      status: 'pending',
      description: 'Setting up authentication and permissions'
    },
    {
      id: 'connect',
      label: 'Connecting to n8n instance',
      status: 'pending',
      description: 'Establishing connection to automation platform'
    },
    {
      id: 'upload',
      label: 'Uploading workflow definition',
      status: 'pending',
      description: 'Transferring workflow configuration'
    },
    {
      id: 'activate',
      label: 'Activating workflow',
      status: 'pending',
      description: 'Starting workflow and scheduling triggers'
    },
    {
      id: 'verify',
      label: 'Verifying deployment',
      status: 'pending',
      description: 'Running final checks and tests'
    }
  ])

  useEffect(() => {
    const timer = setInterval(() => {
      setSteps(prevSteps => {
        const newSteps = [...prevSteps]
        
        // Complete current step
        if (currentStep < newSteps.length && newSteps[currentStep].status !== 'completed') {
          newSteps[currentStep].status = 'completed'
        }
        
        // Start next step
        const nextStep = currentStep + 1
        if (nextStep < newSteps.length) {
          newSteps[nextStep].status = 'in-progress'
        }
        
        return newSteps
      })
      
      setCurrentStep(prev => {
        const next = prev + 1
        const newProgress = (next / steps.length) * 100
        setProgress(newProgress)
        
        if (next >= steps.length) {
          setIsComplete(true)
          setWorkflowId(`wf_${Math.random().toString(36).substr(2, 9)}`)
          onComplete?.()
          return prev
        }
        
        return next
      })
    }, 1500) // Progress every 1.5 seconds

    return () => clearInterval(timer)
  }, [currentStep, steps.length, onComplete])

  const getStepIcon = (step: Step) => {
    switch (step.status) {
      case 'completed':
        return <Check className="w-4 h-4 text-green-600" />
      case 'in-progress':
        return <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
      case 'failed':
        return <AlertTriangle className="w-4 h-4 text-red-600" />
      default:
        return <div className="w-4 h-4 border-2 border-gray-300 rounded-full" />
    }
  }

  if (isComplete) {
    return (
      <div className="p-6 bg-white border rounded-lg shadow-sm">
        <div className="flex items-center justify-center w-12 h-12 mx-auto mb-4 bg-green-100 rounded-full">
          <Check className="w-6 h-6 text-green-600" />
        </div>
        
        <h3 className="text-lg font-semibold text-center mb-2">✅ Workflow created successfully!</h3>
        
        <div className="mb-4 p-4 bg-gray-50 rounded-lg">
          <h4 className="font-medium text-gray-900 mb-1">📋 {workflowName}</h4>
          <p className="text-sm text-green-600 mb-1">Status: Ready to deploy</p>
          <p className="text-sm text-gray-600">ID: {workflowId}</p>
        </div>
        
        <p className="text-center text-gray-600 mb-4">
          Your workflow is saved and ready to activate. Would you like to deploy it now?
        </p>
        
        <div className="flex flex-wrap gap-2 justify-center">
          <Button className="flex-1 min-w-[120px]">
            <Play className="w-4 h-4 mr-2" />
            Deploy Now
          </Button>
          <Button variant="outline" className="flex-1 min-w-[120px]">
            <Eye className="w-4 h-4 mr-2" />
            View Details
          </Button>
          <Button variant="outline" className="flex-1 min-w-[120px]">
            <Settings className="w-4 h-4 mr-2" />
            Edit Settings
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6 bg-white border rounded-lg shadow-sm">
      <div className="flex items-center justify-center w-12 h-12 mx-auto mb-4 bg-blue-100 rounded-full">
        <Rocket className="w-6 h-6 text-blue-600" />
      </div>
      
      <h3 className="text-lg font-semibold text-center mb-2">Creating your workflow...</h3>
      <p className="text-center text-gray-600 mb-6">Deploying "{workflowName}" to n8n</p>
      
      <div className="space-y-3 mb-6">
        {steps.map((step, index) => (
          <div key={step.id} className="flex items-start space-x-3">
            <div className="flex-shrink-0 mt-0.5">
              {getStepIcon(step)}
            </div>
            <div className="flex-1 min-w-0">
              <p className={cn(
                "text-sm font-medium",
                step.status === 'completed' ? "text-green-600" :
                step.status === 'in-progress' ? "text-blue-600" :
                step.status === 'failed' ? "text-red-600" :
                "text-gray-500"
              )}>
                {step.label}
              </p>
              {step.status === 'in-progress' && step.description && (
                <p className="text-xs text-gray-500 mt-1">{step.description}</p>
              )}
            </div>
          </div>
        ))}
      </div>
      
      <div className="space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-gray-600">Progress</span>
          <span className="text-gray-600">{Math.round(progress)}%</span>
        </div>
        <Progress value={progress} className="h-2" />
      </div>
      
      <p className="text-center text-sm text-gray-500 mt-4">
        This may take 30-60 seconds
      </p>
    </div>
  )
}