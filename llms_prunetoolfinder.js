/**
 * llms_prunetoolfinder.js
 * ========================
 * PruneTool — Model Suggestion Config
 *
 * HOW TO USE:
 *   List the AI models YOU have access to.
 *   PruneTool uses this to suggest which model to use based on query
 *   complexity, and to track daily token usage per model.
 *
 * FIELDS:
 *   id              — unique name for this entry (no spaces)
 *   label           — friendly display name shown in terminal + dashboard
 *   model           — exact model name / ID your client uses
 *   complexity      — which query type this model handles best:
 *                       "simple"  → basic edit, rename, quick lookup
 *                       "medium"  → refactor, multi-file change, debugging
 *                       "complex" → architecture, large codebase reasoning, design
 *   dailyTokenGoal  — alert you at 90% of this limit today (set 0 to disable)
 *                       Examples: 50000 (50k), 100000 (100k), 500000 (500k)
 *
 * NOTE:
 *   You do NOT need to add Scout (Groq/Llama) here.
 *   Scout is PruneTool's internal tool, configured separately.
 */

module.exports = {

  models: [

    // ── EXAMPLES — uncomment and edit what you have ──────────────────

    // Claude Haiku — fast, cheap, simple tasks
    // { id: "claude-haiku",  label: "Claude Haiku 4.5",      model: "claude-haiku-4-5-20251001", complexity: "simple",  dailyTokenGoal: 20000 },

    // Claude Sonnet — medium reasoning
    // { id: "claude-sonnet", label: "Claude Sonnet 4.6",     model: "claude-sonnet-4-6",         complexity: "medium",  dailyTokenGoal: 10000 },

    // Claude Opus — highest reasoning, large codebases
    // { id: "claude-opus",   label: "Claude Opus 4.6",       model: "claude-opus-4-6",           complexity: "complex", dailyTokenGoal: 20000 },

    // Google Gemini Flash — fast and cheap
    // { id: "gemini-flash",  label: "Gemini 2.0 Flash",      model: "gemini-2.0-flash",          complexity: "simple",  dailyTokenGoal: 500000 },

    // Google Gemini Pro — medium complexity
    // { id: "gemini-pro",    label: "Gemini 1.5 Pro",        model: "gemini-1.5-pro",            complexity: "medium",  dailyTokenGoal: 100000 },

    // OpenAI GPT-4o
    // { id: "gpt-4o",        label: "GPT-4o",                model: "gpt-4o",                    complexity: "medium",  dailyTokenGoal: 50000  },

    // Ollama local — no cost
    // { id: "ollama-llama",  label: "Llama 3.1 8B (local)",  model: "llama3.1:8b",               complexity: "simple",  dailyTokenGoal: 0      },

    // ── ADD YOUR MODELS BELOW ─────────────────────────────────────────

  ]

};
