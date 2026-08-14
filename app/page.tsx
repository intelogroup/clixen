"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { 
  ArrowRight, 
  Bot, 
  CheckCircle, 
  Zap, 
  Globe, 
  Shield, 
  Mail,
  Calendar,
  Database,
  Cloud,
  BarChart3,
  Workflow,
  Terminal,
  Cpu
} from "lucide-react"

export default function LandingPage() {
  const router = useRouter()
  const [isScrolled, setIsScrolled] = useState(false)

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 50)
    }
    window.addEventListener('scroll', handleScroll)
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  const features = [
    {
      icon: Bot,
      title: "Local AI Assistant",
      description: "Chat with an assistant that can read your files, run tools, and check your workflows — right on this machine."
    },
    {
      icon: Zap,
      title: "Natural Language Automation",
      description: "Describe what you want in plain English. Clixen turns it into a real, running workflow."
    },
    {
      icon: Globe,
      title: "Tools & Integrations",
      description: "Email, file search, web search, reminders, and more — wired in and ready to use."
    },
    {
      icon: Shield,
      title: "Private by Design",
      description: "Data stays local. No cloud account, no subscription, no analytics to a third party."
    }
  ]

  const useCases = [
    {
      icon: Mail,
      title: "Email",
      description: "Watch, summarize, and reply to your inbox",
      color: "text-blue-600 bg-blue-100"
    },
    {
      icon: Calendar,
      title: "Reminders",
      description: "Scheduled messages, notes, and alerts",
      color: "text-green-600 bg-green-100"
    },
    {
      icon: Database,
      title: "Files & Notes",
      description: "Search, summarize, and organize local documents",
      color: "text-purple-600 bg-purple-100"
    },
    {
      icon: Cloud,
      title: "Web",
      description: "Search and pull information from the internet",
      color: "text-orange-600 bg-orange-100"
    },
    {
      icon: BarChart3,
      title: "Reports",
      description: "Digests, charts, and scheduled summaries",
      color: "text-pink-600 bg-pink-100"
    },
    {
      icon: Workflow,
      title: "Automations",
      description: "Trigger workflows from email, schedules, and more",
      color: "text-indigo-600 bg-indigo-100"
    }
  ]

  const runtimeFacts = [
    {
      icon: Cpu,
      title: "Runs on your machine",
      description: "Ollama for local models, cloud fallback for heavy reasoning.",
    },
    {
      icon: Terminal,
      title: "A real agent harness",
      description: "Tools, skills, task workers, and automations — all supervised locally.",
    },
  ]

  return (
    <div className="min-h-screen bg-white">
      {/* Navigation */}
      <nav className={`fixed top-0 w-full z-50 transition-all duration-300 ${
        isScrolled ? 'bg-white/95 backdrop-blur-md shadow-sm' : 'bg-transparent'
      }`}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <div className="flex items-center space-x-2">
              <Bot className="h-8 w-8 text-blue-600" />
              <span className="text-xl font-bold text-gray-900">Clixen</span>
            </div>
            
            <div className="hidden md:flex items-center space-x-8">
              <a href="#features" className="text-gray-600 hover:text-gray-900 transition-colors">Features</a>
              <a href="#use-cases" className="text-gray-600 hover:text-gray-900 transition-colors">Use Cases</a>
              <a href="#runtime" className="text-gray-600 hover:text-gray-900 transition-colors">How It Runs</a>
            </div>

            <div className="flex items-center space-x-4">
              <Button 
                variant="ghost" 
                onClick={() => router.push('/auth/signin')}
                className="hidden sm:inline-flex"
              >
                Sign In
              </Button>
              <Button 
                onClick={() => router.push('/auth/signin')}
                className="bg-blue-600 hover:bg-blue-700"
              >
                Open App
              </Button>
            </div>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="pt-32 pb-20 px-4 sm:px-6 lg:px-8 bg-gradient-to-br from-blue-50 via-white to-purple-50">
        <div className="max-w-7xl mx-auto text-center">
          <div className="max-w-3xl mx-auto">
            <Badge className="mb-6 bg-blue-100 text-blue-800 border-blue-200">
              Local-first AI workspace
            </Badge>
            
            <h1 className="text-4xl md:text-6xl font-bold text-gray-900 mb-6 leading-tight">
              Your own AI agent,
              <span className="bg-gradient-to-r from-blue-600 to-purple-600 bg-clip-text text-transparent"> running locally</span>
            </h1>
            
            <p className="text-xl text-gray-600 mb-8 leading-relaxed">
              Clixen is a personal AI workspace that lives on your machine. Chat with an
              assistant that can read your files, run tools, and automate your workflows —
              no cloud subscription required.
            </p>

            <div className="flex flex-col sm:flex-row gap-4 justify-center mb-12">
              <Button 
                size="lg" 
                onClick={() => router.push('/auth/signin')}
                className="bg-blue-600 hover:bg-blue-700 h-12 px-8 text-lg"
              >
                Open Your Workspace
                <ArrowRight className="ml-2 h-5 w-5" />
              </Button>
            </div>

            <div className="flex items-center justify-center space-x-6 text-sm text-gray-500">
              <div className="flex items-center">
                <CheckCircle className="h-4 w-4 text-green-500 mr-2" />
                Free forever
              </div>
              <div className="flex items-center">
                <CheckCircle className="h-4 w-4 text-green-500 mr-2" />
                Runs locally
              </div>
              <div className="flex items-center">
                <CheckCircle className="h-4 w-4 text-green-500 mr-2" />
                Your data stays put
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="py-20 px-4 sm:px-6 lg:px-8 bg-white">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">
              What Clixen can do
            </h2>
            <p className="text-xl text-gray-600 max-w-3xl mx-auto">
              A single workspace for talking to your files, running automations, and getting real work done.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
            {features.map((feature, index) => (
              <Card key={index} className="group hover:shadow-lg transition-all duration-300 border-2 hover:border-blue-200">
                <CardContent className="p-6 text-center">
                  <div className="inline-flex items-center justify-center w-12 h-12 bg-blue-100 rounded-lg mb-4 group-hover:bg-blue-200 transition-colors">
                    <feature.icon className="h-6 w-6 text-blue-600" />
                  </div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-2">{feature.title}</h3>
                  <p className="text-gray-600 text-sm leading-relaxed">{feature.description}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Use Cases Section */}
      <section id="use-cases" className="py-20 px-4 sm:px-6 lg:px-8 bg-gray-50">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">
              Real things you can do
            </h2>
            <p className="text-xl text-gray-600 max-w-3xl mx-auto">
              Connected to the tools already on this machine.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {useCases.map((useCase, index) => (
              <Card key={index} className="group hover:shadow-lg transition-all duration-300 border-2 hover:border-gray-300">
                <CardContent className="p-6">
                  <div className={`inline-flex items-center justify-center w-12 h-12 rounded-lg mb-4 ${useCase.color}`}>
                    <useCase.icon className="h-6 w-6" />
                  </div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-2">{useCase.title}</h3>
                  <p className="text-gray-600 text-sm">{useCase.description}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Runtime Section */}
      <section id="runtime" className="py-20 px-4 sm:px-6 lg:px-8 bg-white">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">
              How it runs
            </h2>
            <p className="text-xl text-gray-600 max-w-3xl mx-auto">
              No servers, no setup wizard, no billing page. It's a process on this machine.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {runtimeFacts.map((fact, index) => (
              <Card key={index} className="border-2 hover:shadow-lg transition-all duration-300">
                <CardContent className="p-6 flex items-start gap-4">
                  <div className="p-3 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600 shrink-0">
                    <fact.icon className="h-6 w-6 text-white" />
                  </div>
                  <div>
                    <h3 className="text-lg font-semibold text-gray-900 mb-1">{fact.title}</h3>
                    <p className="text-gray-600 text-sm leading-relaxed">{fact.description}</p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-20 px-4 sm:px-6 lg:px-8 bg-gradient-to-r from-blue-600 to-purple-600">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-6">
            Ready to use it?
          </h2>
          <p className="text-xl text-blue-100 mb-8">
            Sign in to your local workspace and start a chat.
          </p>
          
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Button 
              size="lg"
              onClick={() => router.push('/auth/signin')}
              className="bg-white text-blue-600 hover:bg-gray-100 h-12 px-8 text-lg"
            >
              Open App
              <ArrowRight className="ml-2 h-5 w-5" />
            </Button>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-gray-900 text-white py-12 px-4 sm:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            <div>
              <div className="flex items-center space-x-2 mb-4">
                <Bot className="h-6 w-6 text-blue-400" />
                <span className="text-lg font-bold">Clixen</span>
              </div>
              <p className="text-gray-400 text-sm">
                A local-first AI workspace: assistant, tools, and automations running on your machine.
              </p>
            </div>
            
            <div>
              <h3 className="font-semibold mb-4">App</h3>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><a href="#features" className="hover:text-white transition-colors">Features</a></li>
                <li><a href="#use-cases" className="hover:text-white transition-colors">Use Cases</a></li>
                <li><a href="#runtime" className="hover:text-white transition-colors">How It Runs</a></li>
              </ul>
            </div>
            
            <div>
              <h3 className="font-semibold mb-4">Access</h3>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><a href="/auth/signin" className="hover:text-white transition-colors">Sign In</a></li>
                <li><a href="/auth/signup" className="hover:text-white transition-colors">Set Up Workspace</a></li>
              </ul>
            </div>
          </div>
          
          <div className="border-t border-gray-800 mt-8 pt-8 text-center">
            <p className="text-gray-400 text-sm">
              Clixen — runs locally, free forever.
            </p>
          </div>
        </div>
      </footer>
    </div>
  )
}
