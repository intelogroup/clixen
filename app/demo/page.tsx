'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { 
  ArrowLeft,
  BarChart3,
  CircleDot,
  PauseCircle,
  Circle,
  MoreVertical,
  Play,
  Pause,
  Edit,
  User,
  Menu
} from 'lucide-react'
import { DashboardSidebar } from '@/components/dashboard/dashboard-sidebar'
import { MobileSidebar } from '@/components/ui/mobile-sidebar'

const sampleWorkflows = [
  {
    id: 'wf_001',
    name: 'Daily Email Report',
    description: 'Analytics summary every morning',
    status: 'active' as const,
    schedule: 'Daily 9:00 AM',
    nextRun: 'in 14h',
    successRate: 98.4,
    totalRuns: 127,
    lastRun: '2h ago',
    category: 'Analytics'
  },
  {
    id: 'wf_002',
    name: 'Customer Onboarding',
    description: 'Multi-step automation for new signups',
    status: 'active' as const,
    trigger: 'Webhook',
    lastTriggered: '2h ago',
    successRate: 89,
    totalRuns: 89,
    failures: 5,
    category: 'Automation'
  },
  {
    id: 'wf_003',
    name: 'Weekly Backup',
    description: 'Database backup to cloud storage',
    status: 'paused' as const,
    schedule: 'Sundays 2:00 AM',
    successRate: 100,
    totalRuns: 52,
    category: 'Maintenance'
  },
  {
    id: 'wf_004',
    name: 'Social Media Monitor',
    description: 'Track mentions and respond automatically',
    status: 'draft' as const,
    created: 'Yesterday',
    category: 'Social'
  }
]

export default function DemoPage() {
  const [activeTab, setActiveTab] = useState<'active' | 'draft' | 'all'>('all')
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false)

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'active':
        return <CircleDot className="w-4 h-4 text-green-500" />
      case 'paused':
        return <PauseCircle className="w-4 h-4 text-blue-500" />
      case 'draft':
        return <Circle className="w-4 h-4 text-gray-400" />
      default:
        return <Circle className="w-4 h-4 text-gray-400" />
    }
  }

  const getStatusBadge = (status: string) => {
    const variants = {
      active: 'default',
      paused: 'secondary',
      draft: 'outline'
    } as const

    return (
      <Badge variant={variants[status as keyof typeof variants] || 'outline'}>
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </Badge>
    )
  }

  const filteredWorkflows = sampleWorkflows.filter(workflow => {
    if (activeTab === 'all') return true
    return workflow.status === activeTab
  })

  const statusCounts = {
    active: sampleWorkflows.filter(w => w.status === 'active').length,
    draft: sampleWorkflows.filter(w => w.status === 'draft').length,
    all: sampleWorkflows.length
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="flex">
        <MobileSidebar 
          isOpen={isMobileSidebarOpen} 
          onClose={() => setIsMobileSidebarOpen(false)} 
        />
        
        <DashboardSidebar />
        
        <div className="flex-1 lg:ml-80">
          {/* Header */}
          <header className="bg-white border-b px-6 py-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-4">
                <Button 
                  variant="ghost" 
                  size="sm"
                  className="lg:hidden"
                  onClick={() => setIsMobileSidebarOpen(true)}
                >
                  <Menu className="h-5 w-5" />
                </Button>
                <Link href="/">
                  <Button variant="ghost" size="sm">
                    <ArrowLeft className="w-4 h-4 mr-2" />
                    Back to Home
                  </Button>
                </Link>
                <h1 className="text-xl font-semibold">Demo Dashboard</h1>
              </div>
              <div className="flex items-center space-x-2">
                <Button variant="outline" size="sm">
                  <BarChart3 className="w-4 h-4 mr-2" />
                  Analytics
                </Button>
                <Button variant="outline" size="sm">
                  <User className="w-4 h-4 mr-2" />
                  Profile
                </Button>
              </div>
            </div>
          </header>

          {/* Main Content */}
          <main className="p-6">
            {/* Workflow Controls */}
            <div className="mb-6 bg-white rounded-lg border p-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div className="flex items-center space-x-4">
                  <Button
                    variant={activeTab === 'active' ? 'default' : 'ghost'}
                    onClick={() => setActiveTab('active')}
                    size="sm"
                  >
                    Active ({statusCounts.active})
                  </Button>
                  <Button
                    variant={activeTab === 'draft' ? 'default' : 'ghost'}
                    onClick={() => setActiveTab('draft')}
                    size="sm"
                  >
                    Draft ({statusCounts.draft})
                  </Button>
                  <Button
                    variant={activeTab === 'all' ? 'default' : 'ghost'}
                    onClick={() => setActiveTab('all')}
                    size="sm"
                  >
                    All ({statusCounts.all})
                  </Button>
                </div>
                <div className="flex items-center space-x-2">
                  <Link href="/chat">
                    <Button>+ Create New</Button>
                  </Link>
                </div>
              </div>
            </div>

            {/* Workflow List */}
            <div className="space-y-4">
              {filteredWorkflows.map((workflow) => (
                <Card key={workflow.id} className="transition-shadow hover:shadow-md">
                  <CardHeader className="pb-3">
                    <div className="flex items-start justify-between">
                      <div className="flex items-start space-x-3">
                        {getStatusIcon(workflow.status)}
                        <div>
                          <CardTitle className="text-lg">{workflow.name}</CardTitle>
                          <CardDescription className="mt-1">
                            {workflow.description}
                          </CardDescription>
                        </div>
                      </div>
                      <div className="flex items-center space-x-2">
                        {getStatusBadge(workflow.status)}
                        <Button variant="ghost" size="sm">
                          <MoreVertical className="w-4 h-4" />
                        </Button>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                      <div>
                        <p className="text-sm text-gray-600 mb-1">Schedule/Trigger</p>
                        <p className="text-sm font-medium">
                          {workflow.schedule || workflow.trigger || 'Manual'}
                          {workflow.nextRun && (
                            <span className="text-gray-500"> • Next: {workflow.nextRun}</span>
                          )}
                          {workflow.lastTriggered && (
                            <span className="text-gray-500"> • Last: {workflow.lastTriggered}</span>
                          )}
                          {workflow.created && (
                            <span className="text-gray-500"> • Created: {workflow.created}</span>
                          )}
                        </p>
                      </div>
                      
                      {workflow.status !== 'draft' && (
                        <>
                          <div>
                            <p className="text-sm text-gray-600 mb-1">Performance</p>
                            <div className="flex items-center space-x-2">
                              <Progress value={workflow.successRate} className="h-2 flex-1" />
                              <span className="text-sm font-medium">
                                {workflow.totalRuns} runs
                                {workflow.failures && workflow.failures > 0 && (
                                  <span className="text-red-500"> • {workflow.failures} failures</span>
                                )}
                              </span>
                            </div>
                          </div>
                          
                          <div className="flex items-center justify-end space-x-1">
                            <Button variant="outline" size="sm">
                              <Edit className="w-3 h-3 mr-1" />
                              Edit
                            </Button>
                            {workflow.status === 'active' ? (
                              <Button variant="outline" size="sm">
                                <Pause className="w-3 h-3 mr-1" />
                                Pause
                              </Button>
                            ) : (
                              <Button variant="outline" size="sm">
                                <Play className="w-3 h-3 mr-1" />
                                Resume
                              </Button>
                            )}
                          </div>
                        </>
                      )}
                      
                      {workflow.status === 'draft' && (
                        <div className="md:col-span-2 flex items-center justify-end space-x-2">
                          <p className="text-sm text-gray-600 mr-4">Ready for deployment</p>
                          <Button size="sm">
                            <Play className="w-3 h-3 mr-1" />
                            Deploy Workflow
                          </Button>
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>

            {filteredWorkflows.length === 0 && (
              <div className="text-center py-12">
                <p className="text-gray-500 mb-4">No {activeTab} workflows found</p>
                <Link href="/chat">
                  <Button>Create Your First Workflow</Button>
                </Link>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}