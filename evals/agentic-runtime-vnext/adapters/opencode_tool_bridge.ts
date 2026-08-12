// OpenCode 自定义工具到统一 Python Tool Bridge 的唯一通道。
export async function callTool(toolName: string, args: Record<string, unknown>) {
  const processHandle = Bun.spawn(
    [
      process.env.MANGROVE_BAKEOFF_PYTHON!,
      process.env.MANGROVE_BAKEOFF_TOOL_HOST!,
      "--case-file",
      process.env.MANGROVE_BAKEOFF_CASE_FILE!,
      "--case-id",
      process.env.MANGROVE_BAKEOFF_CASE_ID!,
      "--run-dir",
      process.env.MANGROVE_BAKEOFF_RUN_DIR!,
      "call",
      toolName,
    ],
    {
      stdin: new TextEncoder().encode(JSON.stringify(args)),
      stdout: "pipe",
      stderr: "pipe",
      env: process.env,
    },
  )
  const stdout = await new Response(processHandle.stdout).text()
  const stderr = await new Response(processHandle.stderr).text()
  const exitCode = await processHandle.exited
  if (exitCode !== 0) {
    throw new Error(stderr.trim() || "Tool Bridge 调用失败")
  }
  return JSON.parse(stdout)
}
