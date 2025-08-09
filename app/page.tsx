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
  Users, 
  Globe, 
  Shield, 
  Star,
  PlayCircle,
  ChevronDown,
  Mail,
  Calendar,
  Database,
  Cloud,
  BarChart3,
  Workflow
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
      title: "AI-Powered Automation",
      description: "Simply describe what you want to automate in natural language, and Clixen creates it for you."
    },
    {
      icon: Zap,
      title: "Lightning Fast Setup",
      description: "Go from idea to working automation in under 5 minutes. No coding or technical knowledge required."
    },
    {
      icon: Globe,
      title: "400+ Integrations",
      description: "Connect with all your favorite tools - from Gmail and Slack to Salesforce and Shopify."
    },
    {
      icon: Shield,
      title: "Enterprise Security",
      description: "Bank-level encryption and security. Your data is safe and your workflows are reliable."
    }
  ]

  const useCases = [
    {
      icon: Mail,
      title: "Email Automation",
      description: "Welcome sequences, follow-ups, newsletters",
      color: "text-blue-600 bg-blue-100"
    },
    {
      icon: Calendar,
      title: "Scheduling & Reminders",
      description: "Appointment booking, task reminders, events",
      color: "text-green-600 bg-green-100"
    },
    {
      icon: Database,
      title: "Data Management",
      description: "CRM sync, lead scoring, data enrichment",
      color: "text-purple-600 bg-purple-100"
    },
    {
      icon: Cloud,
      title: "File Operations",
      description: "Backup, sync, organization, compression",
      color: "text-orange-600 bg-orange-100"
    },
    {
      icon: BarChart3,
      title: "Analytics & Reporting",
      description: "Daily reports, performance tracking, alerts",
      color: "text-pink-600 bg-pink-100"
    },
    {
      icon: Workflow,
      title: "Business Processes",
      description: "Customer onboarding, order processing, approvals",
      color: "text-indigo-600 bg-indigo-100"
    }
  ]

  const testimonials = [
    {
      name: "Sarah Chen",
      role: "Marketing Director",
      company: "TechFlow",
      content: "Clixen saved us 20 hours per week on manual tasks. The natural language interface is incredible!",
      rating: 5
    },
    {
      name: "Michael Rodriguez",
      role: "Operations Manager", 
      company: "StartupCo",
      content: "Setup took 3 minutes and we immediately saw results. Best automation tool we've ever used.",
      rating: 5
    },
    {
      name: "Emily Watson",
      role: "CEO",
      company: "GrowthLabs",
      content: "Game-changer for our business. Clixen handles complex workflows that would take weeks to build manually.",
      rating: 5
    }
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
              <a href="#testimonials" className="text-gray-600 hover:text-gray-900 transition-colors">Testimonials</a>
              <a href="#pricing" className="text-gray-600 hover:text-gray-900 transition-colors">Pricing</a>
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
                onClick={() => router.push('/auth/signup')}
                className="bg-blue-600 hover:bg-blue-700"
              >
                Get Started Free
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
              ✨ Now powered by advanced AI
            </Badge>
            
            <h1 className="text-4xl md:text-6xl font-bold text-gray-900 mb-6 leading-tight">
              Automate Anything with
              <span className="bg-gradient-to-r from-blue-600 to-purple-600 bg-clip-text text-transparent"> Natural Language</span>
            </h1>
            
            <p className="text-xl text-gray-600 mb-8 leading-relaxed">
              Stop wasting time on repetitive tasks. Just tell Clixen what you want to automate in plain English, 
              and watch as it creates powerful workflows in minutes, not hours.
            </p>

            <div className="flex flex-col sm:flex-row gap-4 justify-center mb-12">
              <Button 
                size="lg" 
                onClick={() => router.push('/auth/signup')}
                className="bg-blue-600 hover:bg-blue-700 h-12 px-8 text-lg"
              >
                Start Free Trial
                <ArrowRight className="ml-2 h-5 w-5" />
              </Button>
              <Button 
                size="lg" 
                variant="outline"
                onClick={() => router.push('/chat')}
                className="h-12 px-8 text-lg border-2"
              >
                <PlayCircle className="mr-2 h-5 w-5" />
                Try Demo
              </Button>
            </div>

            <div className="flex items-center justify-center space-x-6 text-sm text-gray-500">
              <div className="flex items-center">
                <CheckCircle className="h-4 w-4 text-green-500 mr-2" />
                Free 14-day trial
              </div>
              <div className="flex items-center">
                <CheckCircle className="h-4 w-4 text-green-500 mr-2" />
                No credit card required
              </div>
              <div className="flex items-center">
                <CheckCircle className="h-4 w-4 text-green-500 mr-2" />
                Cancel anytime
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
              Why Choose Clixen?
            </h2>
            <p className="text-xl text-gray-600 max-w-3xl mx-auto">
              Experience the future of automation with our AI-powered platform that understands what you need and builds it for you.
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
              Automate Every Part of Your Business
            </h2>
            <p className="text-xl text-gray-600 max-w-3xl mx-auto">
              From simple email sequences to complex business processes, Clixen handles it all with ease.
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

          <div className="text-center mt-12">
            <Button 
              size="lg"
              onClick={() => router.push('/chat')}
              className="bg-blue-600 hover:bg-blue-700"
            >
              Explore All Use Cases
              <ArrowRight className="ml-2 h-5 w-5" />
            </Button>
          </div>
        </div>
      </section>

      {/* Testimonials Section */}
      <section id="testimonials" className="py-20 px-4 sm:px-6 lg:px-8 bg-white">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">
              Loved by Thousands of Teams
            </h2>
            <p className="text-xl text-gray-600">
              See what our customers are saying about Clixen
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {testimonials.map((testimonial, index) => (
              <Card key={index} className="border-2">
                <CardContent className="p-6">
                  <div className="flex items-center mb-4">
                    {[...Array(testimonial.rating)].map((_, i) => (
                      <Star key={i} className="h-4 w-4 text-yellow-400 fill-current" />
                    ))}
                  </div>
                  <p className="text-gray-700 mb-4 italic">"{testimonial.content}"</p>
                  <div>
                    <div className="font-semibold text-gray-900">{testimonial.name}</div>
                    <div className="text-sm text-gray-600">{testimonial.role} at {testimonial.company}</div>
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
            Ready to Transform Your Workflow?
          </h2>
          <p className="text-xl text-blue-100 mb-8">
            Join thousands of teams already automating their success with Clixen.
            Start your free trial today and see the difference in minutes.
          </p>
          
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Button 
              size="lg"
              onClick={() => router.push('/auth/signup')}
              className="bg-white text-blue-600 hover:bg-gray-100 h-12 px-8 text-lg"
            >
              Start Free Trial
              <ArrowRight className="ml-2 h-5 w-5" />
            </Button>
            <Button 
              size="lg"
              variant="outline"
              onClick={() => router.push('/chat')}
              className="border-2 border-white text-white hover:bg-white hover:text-blue-600 h-12 px-8 text-lg"
            >
              <PlayCircle className="mr-2 h-5 w-5" />
              Try Interactive Demo
            </Button>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-gray-900 text-white py-12 px-4 sm:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
            <div>
              <div className="flex items-center space-x-2 mb-4">
                <Bot className="h-6 w-6 text-blue-400" />
                <span className="text-lg font-bold">Clixen</span>
              </div>
              <p className="text-gray-400 text-sm">
                The AI-powered automation platform that understands natural language and creates workflows for you.
              </p>
            </div>
            
            <div>
              <h3 className="font-semibold mb-4">Product</h3>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><a href="#features" className="hover:text-white transition-colors">Features</a></li>
                <li><a href="#use-cases" className="hover:text-white transition-colors">Use Cases</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Integrations</a></li>
                <li><a href="#" className="hover:text-white transition-colors">API</a></li>
              </ul>
            </div>
            
            <div>
              <h3 className="font-semibold mb-4">Company</h3>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><a href="#" className="hover:text-white transition-colors">About</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Blog</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Careers</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Contact</a></li>
              </ul>
            </div>
            
            <div>
              <h3 className="font-semibold mb-4">Support</h3>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><a href="#" className="hover:text-white transition-colors">Help Center</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Documentation</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Community</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Status</a></li>
              </ul>
            </div>
          </div>
          
          <div className="border-t border-gray-800 mt-8 pt-8 text-center">
            <p className="text-gray-400 text-sm">
              © 2025 Clixen. All rights reserved. Built with ❤️ for automation enthusiasts.
            </p>
          </div>
        </div>
      </footer>
    </div>
  )
}
