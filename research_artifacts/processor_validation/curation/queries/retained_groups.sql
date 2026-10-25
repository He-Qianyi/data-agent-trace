SELECT
  CASE benchmark WHEN 'dabstep' THEN 'DABstep' WHEN 'dataagentbench' THEN 'DataAgent' ELSE 'Agentic' END || '/' ||
  CASE agent_id WHEN 'claude_code' THEN 'Claude' WHEN 'codex' THEN 'Codex' ELSE 'Kimi' END AS "group",
  benchmark, agent_id AS agent,
  count(*) FILTER (WHERE decision = 'retained') AS retained,
  count(*) FILTER (WHERE decision = 'excluded') AS excluded,
  count(*) AS input_count
FROM curation_decisions
GROUP BY benchmark, agent_id
ORDER BY benchmark, agent_id
