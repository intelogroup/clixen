"use client"

import { useState } from "react"
import { OAuthPermissionModal } from "./oauth-permission-modal"
import { APISetupModal } from "./api-setup-modal"

export type ModalType = 
  | "oauth-permission"
  | "api-setup"
  | null

interface ModalManagerProps {
  type: ModalType
  service?: string
  onComplete?: (result?: any) => void
  onCancel?: () => void
  children?: React.ReactNode
}

export function ModalManager({
  type,
  service = "Google Drive",
  onComplete,
  onCancel,
  children
}: ModalManagerProps) {
  const [showModal, setShowModal] = useState(!!type)

  const handleModalComplete = (result?: any) => {
    setShowModal(false)
    onComplete?.(result)
  }

  const handleModalCancel = () => {
    setShowModal(false)
    onCancel?.()
  }

  // OAuth permissions for different services
  const getOAuthPermissions = (serviceName: string) => {
    const commonPermissions = {
      "Google Drive": [
        { action: "Read your files", allowed: true },
        { action: "Create folders", allowed: true },
        { action: "Upload backups", allowed: true },
        { action: "Delete files (never)", allowed: false }
      ],
      "Gmail": [
        { action: "Read email headers", allowed: true },
        { action: "Send emails", allowed: true },
        { action: "Create labels", allowed: true },
        { action: "Delete emails (never)", allowed: false }
      ],
      "Slack": [
        { action: "Read channel messages", allowed: true },
        { action: "Send messages", allowed: true },
        { action: "Create channels", allowed: true },
        { action: "Delete messages (never)", allowed: false }
      ],
      "Twitter": [
        { action: "Read your tweets", allowed: true },
        { action: "Post tweets", allowed: true },
        { action: "Read followers", allowed: true },
        { action: "Delete tweets (never)", allowed: false }
      ]
    }

    return commonPermissions[serviceName as keyof typeof commonPermissions] || [
      { action: "Read your data", allowed: true },
      { action: "Create content", allowed: true },
      { action: "Send notifications", allowed: true },
      { action: "Delete data (never)", allowed: false }
    ]
  }

  return (
    <>
      {children}
      
      {showModal && type === "oauth-permission" && (
        <OAuthPermissionModal
          isOpen={showModal}
          service={service}
          permissions={getOAuthPermissions(service)}
          onAllow={() => handleModalComplete({ service, granted: true })}
          onCancel={handleModalCancel}
        />
      )}
      
      {showModal && type === "api-setup" && (
        <APISetupModal
          isOpen={showModal}
          onSave={(services) => handleModalComplete({ services })}
          onCancel={handleModalCancel}
          onSkip={() => handleModalComplete({ skipped: true })}
        />
      )}
    </>
  )
}
