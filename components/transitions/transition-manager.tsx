"use client"

import { useState, useEffect } from "react"
import { AuthLoading } from "./auth-loading"
import { WorkflowGeneration } from "./workflow-generation"
import { DashboardSkeleton } from "./dashboard-skeleton"
import { PermissionDialog } from "./permission-dialog"

export type TransitionType = 
  | "auth-loading"
  | "workflow-generation" 
  | "dashboard-skeleton"
  | "permission-dialog"
  | null

interface TransitionManagerProps {
  type: TransitionType
  workflowType?: string
  service?: string
  onComplete?: (result?: any) => void
  onCancel?: () => void
  children?: React.ReactNode
}

export function TransitionManager({
  type,
  workflowType,
  service,
  onComplete,
  onCancel,
  children
}: TransitionManagerProps) {
  const [showTransition, setShowTransition] = useState(!!type)

  useEffect(() => {
    setShowTransition(!!type)
  }, [type])

  const handleTransitionComplete = (result?: any) => {
    setShowTransition(false)
    onComplete?.(result)
  }

  const handleTransitionCancel = () => {
    setShowTransition(false)
    onCancel?.()
  }

  return (
    <>
      {children}
      
      {showTransition && type === "auth-loading" && (
        <AuthLoading onComplete={handleTransitionComplete} />
      )}
      
      {showTransition && type === "workflow-generation" && (
        <WorkflowGeneration 
          workflowType={workflowType}
          onComplete={handleTransitionComplete}
        />
      )}
      
      {showTransition && type === "dashboard-skeleton" && (
        <DashboardSkeleton />
      )}
      
      {showTransition && type === "permission-dialog" && (
        <PermissionDialog
          service={service || "Google Drive"}
          isOpen={showTransition}
          onAllow={handleTransitionComplete}
          onCancel={handleTransitionCancel}
        />
      )}
    </>
  )
}
