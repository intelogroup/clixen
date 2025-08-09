export default function Home() {
  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-4 py-3">
        <div className="flex justify-between items-center max-w-6xl mx-auto">
          <div className="flex items-center space-x-3">
            <span className="text-xl">🔙</span>
            <span className="text-lg font-semibold">🤖 New Workflow Chat</span>
          </div>
          <div className="flex items-center space-x-3">
            <span className="text-xl cursor-pointer">⚙️</span>
            <span className="text-xl cursor-pointer">👤</span>
          </div>
        </div>
      </header>

      {/* Chat Interface */}
      <div className="max-w-4xl mx-auto p-4">
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 h-[calc(100vh-160px)] flex flex-col">
          {/* Chat Messages */}
          <div className="flex-1 p-6 overflow-y-auto space-y-4">
            {/* AI Welcome Message */}
            <div className="flex items-start space-x-3">
              <div className="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center text-white text-sm font-bold">
                🤖
              </div>
              <div className="bg-gray-100 rounded-lg p-4 max-w-xl">
                <p className="text-gray-800">
                  👋 Welcome to Clixen AI! I help you create powerful automation workflows using natural language. 
                  Just tell me what you'd like to automate, and I'll guide you through creating it step by step.
                </p>
                <p className="text-gray-600 mt-2 text-sm">
                  Try something like: "Send me an email every morning with the weather forecast" or "Backup my files to cloud storage weekly"
                </p>
              </div>
            </div>
          </div>

          {/* Input Area */}
          <div className="border-t border-gray-200 p-4">
            <div className="flex space-x-3">
              <input
                type="text"
                placeholder="Describe the workflow you'd like to create..."
                className="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <button className="bg-blue-500 hover:bg-blue-600 text-white px-6 py-2 rounded-lg font-medium">
                Send →
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
