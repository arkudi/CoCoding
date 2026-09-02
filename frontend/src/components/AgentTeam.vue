<script setup lang="ts">
import type { AgentExecution } from '@/types/run'

defineProps<{ executions: AgentExecution[] }>()

const roleCopy: Record<AgentExecution['role'], string> = {
  manager: 'Manager',
  explorer: 'Explorer',
  implementer: 'Implementer',
  reviewer: 'Reviewer',
}

const statusCopy: Record<AgentExecution['status'], string> = {
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function summary(execution: AgentExecution): string | null {
  if (!execution.final_result_json) return null
  try {
    const payload = JSON.parse(execution.final_result_json) as {
      result?: { summary?: unknown }
      final_response?: unknown
      error?: unknown
    }
    if (typeof payload.result?.summary === 'string') return payload.result.summary
    if (typeof payload.final_response === 'string' && payload.final_response) {
      return payload.final_response
    }
    if (typeof payload.error === 'string' && payload.error) return payload.error
  } catch {
    return null
  }
  return null
}
</script>

<template>
  <section v-if="executions.length" class="agent-team" aria-label="智能体协作">
    <div class="team-heading">
      <span>Agent Team</span>
      <small>{{ executions.length }} 个执行单元</small>
    </div>
    <ol>
      <li
        v-for="execution in executions"
        :key="execution.id"
        class="agent-node"
        :class="{ child: execution.parent_execution_id !== null }"
      >
        <span class="role-dot" :data-role="execution.role" aria-hidden="true" />
        <div>
          <div class="agent-meta">
            <strong>{{ roleCopy[execution.role] }}</strong>
            <span :data-status="execution.status">{{ statusCopy[execution.status] }}</span>
            <small>{{ execution.step_count }} 步</small>
          </div>
          <p>{{ execution.task }}</p>
          <small v-if="summary(execution)" class="agent-summary">{{ summary(execution) }}</small>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.agent-team { margin: 12px 0 14px 46px; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: var(--surface); }
.team-heading { display: flex; justify-content: space-between; padding: 9px 12px; border-bottom: 1px solid var(--line); text-transform: uppercase; letter-spacing: .08em; font-size: 11px; }
.team-heading small { color: var(--muted); text-transform: none; letter-spacing: 0; }
ol { list-style: none; margin: 0; padding: 6px 12px 8px; }
.agent-node { display: grid; grid-template-columns: 10px 1fr; gap: 9px; padding: 8px 0; }
.agent-node.child { margin-left: 18px; border-left: 1px solid var(--line); padding-left: 12px; }
.role-dot { width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: #7c8799; }
.role-dot[data-role='manager'] { background: #7c5cff; }
.role-dot[data-role='implementer'] { background: #20a675; }
.role-dot[data-role='reviewer'] { background: #e29a35; }
.agent-meta { display: flex; gap: 8px; align-items: baseline; font-size: 12px; }
.agent-meta span, .agent-meta small { color: var(--muted); }
.agent-meta span[data-status='running'] { color: #7c5cff; }
.agent-meta span[data-status='failed'] { color: var(--danger); }
.agent-node p { margin: 3px 0 0; font-size: 13px; }
.agent-summary { display: block; margin-top: 4px; color: var(--muted); line-height: 1.4; }
</style>
