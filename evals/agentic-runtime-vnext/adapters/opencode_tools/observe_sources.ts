import { tool } from "@opencode-ai/plugin"
import { callTool } from "../tool_bridge"

export default tool({
  description: "观察 GoalContract 允许的来源、可读位置和结构摘要。",
  args: {},
  async execute() {
    return JSON.stringify(await callTool("observe_sources", {}))
  },
})
