"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { 
  Calendar, 
  Mail, 
  Cloud, 
  BarChart, 
  Globe, 
  Users, 
  Zap, 
  Shield,
  Clock,
  Bell,
  Database,
  FileText,
  Target,
  TrendingUp,
  Smartphone,
  Settings
} from "lucide-react"

interface WorkflowExamplesProps {
  onSelectExample: (example: string) => void
}

interface WorkflowExample {
  title: string
  description: string
  complexity: "Simple" | "Medium" | "Complex"
  category: string
  icon: React.ComponentType<any>
  color: string
  prompt: string
  features: string[]
  estimatedTime: string
}

export function WorkflowExamples({ onSelectExample }: WorkflowExamplesProps) {
  const examples: WorkflowExample[] = [
    {
      title: "Daily Weather Email",
      description: "Get personalized weather updates every morning with UV index and air quality",
      complexity: "Simple",
      category: "Personal Productivity",
      icon: Bell,
      color: "text-blue-600",
      prompt: "Send me daily weather emails every morning at 8 AM with temperature, conditions, UV index, and air quality for New York",
      features: ["Weather API integration", "Email scheduling", "Location-based data", "Health recommendations"],
      estimatedTime: "2 minutes"
    },
    {
      title: "Social Media Automation",
      description: "Schedule and post content across multiple platforms with optimal timing",
      complexity: "Medium", 
      category: "Marketing",
      icon: Smartphone,
      color: "text-pink-600",
      prompt: "Create a social media automation that posts to Facebook, Twitter, Instagram, and LinkedIn with smart scheduling",
      features: ["Multi-platform posting", "Content queue", "Optimal timing", "Analytics tracking"],
      estimatedTime: "5 minutes"
    },
    {
      title: "Customer Onboarding Flow",
      description: "Complete automation for new user registration, CRM sync, and follow-ups",
      complexity: "Complex",
      category: "Business Process",
      icon: Users,
      color: "text-green-600",
      prompt: "Set up customer onboarding that sends welcome emails, adds to HubSpot CRM, notifies sales team on Slack, and schedules 3-day follow-up",
      features: ["Webhook triggers", "CRM integration", "Team notifications", "Delayed actions"],
      estimatedTime: "8 minutes"
    },
    {
      title: "Cloud Backup System",
      description: "Automatically backup files between cloud storage services with versioning",
      complexity: "Medium",
      category: "Data Management", 
      icon: Cloud,
      color: "text-purple-600",
      prompt: "Create weekly backup automation from Dropbox to Google Drive with automatic folder organization",
      features: ["Cloud storage sync", "Scheduled backups", "File organization", "Error recovery"],
      estimatedTime: "4 minutes"
    },
    {
      title: "Website Monitoring",
      description: "Monitor website uptime and performance with instant alert notifications",
      complexity: "Medium",
      category: "Operations",
      icon: Globe,
      color: "text-orange-600", 
      prompt: "Set up website monitoring that checks uptime every 5 minutes and sends Slack alerts when down",
      features: ["Uptime monitoring", "Performance checks", "Instant alerts", "Status dashboard"],
      estimatedTime: "3 minutes"
    },
    {
      title: "Lead Scoring & Nurturing",
      description: "Score leads based on behavior and trigger personalized email sequences",
      complexity: "Complex",
      category: "Sales & Marketing",
      icon: Target,
      color: "text-red-600",
      prompt: "Create lead scoring automation that tracks user behavior, assigns scores, and triggers email sequences based on engagement",
      features: ["Behavioral tracking", "Dynamic scoring", "Email sequences", "CRM updates"],
      estimatedTime: "10 minutes"
    },
    {
      title: "Expense Tracking",
      description: "Automatically categorize expenses and generate monthly reports",
      complexity: "Simple",
      category: "Finance",
      icon: BarChart,
      color: "text-indigo-600",
      prompt: "Automate expense tracking by connecting to my bank account and generating monthly reports",
      features: ["Bank integration", "Auto-categorization", "Monthly reports", "Budget alerts"],
      estimatedTime: "3 minutes"
    },
    {
      title: "Task Assignment System",
      description: "Distribute tasks to team members based on workload and expertise",
      complexity: "Complex", 
      category: "Team Management",
      icon: Settings,
      color: "text-teal-600",
      prompt: "Create intelligent task assignment that distributes work based on team capacity and skills",
      features: ["Workload balancing", "Skill matching", "Team notifications", "Progress tracking"],
      estimatedTime: "12 minutes"
    }
  ]

  const getComplexityColor = (complexity: string) => {
    switch (complexity) {
      case "Simple": return "bg-green-100 text-green-800"
      case "Medium": return "bg-yellow-100 text-yellow-800" 
      case "Complex": return "bg-red-100 text-red-800"
      default: return "bg-gray-100 text-gray-800"
    }
  }

  const categories = Array.from(new Set(examples.map(e => e.category)))

  return (
    <div className="space-y-8">
      <div className="text-center">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">🚀 Workflow Examples</h2>
        <p className="text-gray-600 max-w-2xl mx-auto">
          Explore these real-world automation examples. Click any card to start building that workflow right away!
        </p>
      </div>

      {categories.map(category => {
        const categoryExamples = examples.filter(e => e.category === category)
        
        return (
          <div key={category}>
            <h3 className="text-lg font-semibold text-gray-800 mb-4 flex items-center">
              <span className="w-2 h-2 bg-blue-500 rounded-full mr-3" />
              {category}
            </h3>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 lg:gap-6">
              {categoryExamples.map((example, index) => (
                <Card
                  key={index}
                  className="group hover:shadow-lg transition-all duration-200 cursor-pointer border-2 hover:border-blue-200 h-full"
                  onClick={() => onSelectExample(example.prompt)}
                >
                  <CardHeader className="pb-3">
                    <div className="flex items-start justify-between">
                      <div className="flex items-center space-x-3">
                        <div className={`p-2 rounded-lg bg-gray-50 group-hover:bg-blue-50 transition-colors`}>
                          <example.icon className={`h-5 w-5 ${example.color}`} />
                        </div>
                        <div>
                          <CardTitle className="text-base font-semibold text-gray-900">
                            {example.title}
                          </CardTitle>
                          <Badge className={getComplexityColor(example.complexity)}>
                            {example.complexity}
                          </Badge>
                        </div>
                      </div>
                    </div>
                  </CardHeader>
                  
                  <CardContent>
                    <p className="text-sm text-gray-600 mb-4 line-clamp-2">
                      {example.description}
                    </p>
                    
                    <div className="space-y-3">
                      <div className="flex items-center space-x-2 text-xs text-gray-500">
                        <Clock className="h-3 w-3" />
                        <span>~{example.estimatedTime} setup</span>
                      </div>
                      
                      <div className="space-y-1">
                        <div className="text-xs font-medium text-gray-700">Key Features:</div>
                        <div className="flex flex-wrap gap-1">
                          {example.features.slice(0, 3).map((feature, i) => (
                            <Badge key={i} variant="outline" className="text-xs py-0">
                              {feature}
                            </Badge>
                          ))}
                          {example.features.length > 3 && (
                            <Badge variant="outline" className="text-xs py-0">
                              +{example.features.length - 3} more
                            </Badge>
                          )}
                        </div>
                      </div>
                    </div>
                    
                    <Button 
                      className="w-full mt-4 text-sm"
                      onClick={(e) => {
                        e.stopPropagation()
                        onSelectExample(example.prompt)
                      }}
                    >
                      <Zap className="h-3 w-3 mr-2" />
                      Try This Workflow
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )
      })}
      
      {/* Call to Action */}
      <Card className="bg-gradient-to-r from-blue-600 to-purple-600 text-white border-0">
        <CardContent className="p-8 text-center">
          <h3 className="text-xl font-bold mb-2">Don't see what you need?</h3>
          <p className="text-blue-100 mb-4">
            Describe any automation idea in natural language, and I'll help you build it step by step.
          </p>
          <Button 
            variant="secondary" 
            onClick={() => onSelectExample("I need help creating a custom workflow")}
          >
            Create Custom Workflow
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
