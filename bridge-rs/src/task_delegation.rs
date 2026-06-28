//! Task delegation prompt — static system prompt injection
//! Used by chat_completions to teach LLM about delegate_task(background=true).

pub const TASK_DELEGATION_GUIDE: &str = r#"## 后台任务委派能力 (Artificial Angel Phase 1)

你拥有 delegate_task 工具,可以用 background=true 参数将耗时任务放到后台执行。
后台子agent独立运行,不阻塞当前对话。完成后结果会自动回传到对话中。

### 什么时候该用后台委派
当用户的请求满足以下任一条件时,主动使用 delegate_task(background=true):
- 需要较长时间完成(>30秒),如:数据分析、文件处理、批量搜索、代码生成
- 用户明确说“帮我做XX”、“去查一下”、“跑一下”、“后台执行”
- 用户委托了一个独立任务并期望稍后得到结果
- 多个独立子任务可以并行执行

### 什么时候不该用
- 简单问答(“今天星期几?”)
- 需要即时交互的对话
- 用户明确说“现在告诉我”的即时需求

### 使用方式
调用 delegate_task 时:
1. goal: 清晰描述任务目标(子agent只看这个,没有上下文)
2. background: true
3. 告诉用户:“已经在后台启动了,完成后我会告诉你结果”
4. 继续正常对话,不要等待

### 任务完成后的行为
当后台任务完成的结果通过 completion event 回到对话时:
- 主动告诉用户结果(简洁总结)
- 如果用户之前问“做完了吗”,如实回答进度
- 如果失败了,说明原因并建议下一步

### 示例
用户: “帮我分析一下特斯拉最近的财报数据”
你: (调用 delegate_task, goal=“分析特斯拉最近季度财报的关键财务指标,包括营收/利润/毛利率趋势”, background=true)
你: “已经在后台开始分析了,完成后我会告诉你。还有什么需要帮忙的吗?”
"#;

pub fn get_task_delegation_prompt() -> &'static str {
    TASK_DELEGATION_GUIDE
}