"""总指挥层（Conductor）：对话式数据采集分析的编排核心。

注意：本包的 __init__ 故意保持精简、不导入 graph/nodes，
以避免 collectors ↔ conductor 之间的循环导入。
请按需从子模块导入，例如：
    from src.conductor.graph import run_conductor
    from src.conductor.task_spec import TaskSpec
"""
